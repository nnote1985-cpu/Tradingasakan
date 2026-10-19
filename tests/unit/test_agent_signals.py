from decimal import Decimal as D

import pytest

from trading.agent.signals import (
    HOUR_MS,
    MAX_ACTIVE,
    MAX_TTL_MS,
    Signal,
    Status,
    check_new,
    evaluate,
    expired,
    fallback_signal,
    parse_params,
    rsi,
)
from trading.ingest.bars import Bar

T0 = 1_790_000_000_000 - 1_790_000_000_000 % HOUR_MS


def bars(closes, volumes=None, lows=None, highs=None):
    out = []
    for i, c in enumerate(closes):
        c = D(str(c))
        out.append(Bar("BTCUSDT", "1h", T0 + i * HOUR_MS, T0 + (i + 1) * HOUR_MS - 1, c,
                       D(str(highs[i])) if highs else c, D(str(lows[i])) if lows else c, c,
                       D(str(volumes[i])) if volumes else D(1), "agg_1m"))
    return out


def sig(condition, params, created=T0, ttl=MAX_TTL_MS, status=Status.ACTIVE):
    return Signal("s1", condition, {k: D(str(v)) for k, v in params.items()}, created, created + ttl, status)


def now_after(bs):
    return bs[-1].close_time + 1


def test_parse_params_accepts_only_the_allowlist():
    assert parse_params("price_close_above", {"level": "84000"}) == ({"level": D("84000")}, [])
    assert parse_params("exec_python", {"code": "x"})[1] == ["UNKNOWN_CONDITION:exec_python"]
    assert "UNEXPECTED_PARAM:extra" in parse_params("price_touch", {"level": 1, "extra": 2})[1]
    assert parse_params("price_touch", {"level": "nan"})[1] == ["BAD_PARAM:level"]
    assert parse_params("rsi_below", {"value": 120})[1] == ["RSI_OUT_OF_RANGE"]
    assert parse_params("move_pct", {"pct": 0, "hours": 1.5})[1] == ["PCT_ZERO", "HOURS_OUT_OF_RANGE"]
    assert parse_params("volume_spike", {"multiple": 1})[1] == ["MULTIPLE_NOT_ABOVE_1"]


def test_check_new_limits_count_and_lifetime():
    now = T0
    assert check_new("price_touch", {"level": D(1)}, now, now + HOUR_MS, MAX_ACTIVE - 1) == []
    assert check_new("price_touch", {"level": D(1)}, now, now + HOUR_MS, MAX_ACTIVE) == ["TOO_MANY_ACTIVE"]
    assert check_new("price_touch", {"level": D(1)}, now, now + MAX_TTL_MS + 1, 0) == ["EXPIRY_OUT_OF_RANGE"]
    assert check_new("price_touch", {"level": D(1)}, now, now, 0) == ["EXPIRY_OUT_OF_RANGE"]
    assert check_new("time_check", {"at_ms": D(now + 2 * HOUR_MS)}, now, now + HOUR_MS, 0) == ["TIME_CHECK_OUTSIDE_LIFETIME"]


def test_signal_never_fires_on_a_bar_closed_before_it_was_created():
    bs = bars([100, 110])
    s = sig("price_close_above", {"level": 105}, created=bs[-1].close_time + 1)
    assert evaluate(s, bs, now_after(bs)).reason == "NO_NEW_BAR"
    s = sig("price_close_above", {"level": 105}, created=bs[-1].open_time)
    assert evaluate(s, bs, now_after(bs)).fired


def test_price_conditions():
    bs = bars([100, 100], lows=[99, 95], highs=[101, 102])
    n = now_after(bs)
    assert evaluate(sig("price_touch", {"level": 96}), bs, n).fired
    assert not evaluate(sig("price_touch", {"level": 103}), bs, n).fired
    assert evaluate(sig("price_close_below", {"level": 100.5}), bs, n).fired
    assert evaluate(sig("price_close_above", {"level": 100}), bs, n).reason == "NOT_MET"


def test_rsi_matches_wilder_and_needs_enough_bars():
    assert rsi([D(i) for i in range(10)]) is None
    assert rsi([D(i) for i in range(20)]) == 100
    up_down = [D(100 + (i % 2)) for i in range(15)]           # 7 gains of 1, 7 losses of 1
    assert rsi(up_down) == 50
    bs = bars(range(100, 80, -1))
    assert evaluate(sig("rsi_below", {"value": 30}), bs, now_after(bs)).fired
    short = bars(range(5))
    assert evaluate(sig("rsi_below", {"value": 30}), short, now_after(short)).reason == "INSUFFICIENT_DATA"


def test_move_pct_is_signed():
    bs = bars([100, 99, 98, 95])
    n = now_after(bs)
    assert evaluate(sig("move_pct", {"pct": -4, "hours": 3}), bs, n).fired
    assert not evaluate(sig("move_pct", {"pct": -6, "hours": 3}), bs, n).fired
    assert not evaluate(sig("move_pct", {"pct": 4, "hours": 3}), bs, n).fired
    assert evaluate(sig("move_pct", {"pct": -4, "hours": 10}), bs, n).reason == "INSUFFICIENT_DATA"


def test_volume_spike_uses_the_20_bars_before():
    bs = bars([100] * 21, volumes=[10] * 20 + [31])
    assert evaluate(sig("volume_spike", {"multiple": 3}), bs, now_after(bs)).fired
    assert not evaluate(sig("volume_spike", {"multiple": 3.2}), bs, now_after(bs)).fired


def test_funding_needs_a_value():
    bs = bars([100])
    assert evaluate(sig("funding_above", {"rate": "0.0003"}), bs, now_after(bs)).reason == "NO_FUNDING"
    assert evaluate(sig("funding_above", {"rate": "0.0003"}), bs, now_after(bs), D("0.0004")).fired


def test_time_check_fires_on_the_first_bar_closing_at_or_after():
    bs = bars([100, 100])
    assert evaluate(sig("time_check", {"at_ms": bs[-1].close_time}), bs, now_after(bs)).fired
    assert not evaluate(sig("time_check", {"at_ms": bs[-1].close_time + 1}), bs, now_after(bs)).fired


def test_expired_and_inactive_signals_never_fire():
    bs = bars([100, 110])
    s = sig("price_close_above", {"level": 1}, ttl=HOUR_MS)
    assert evaluate(s, bs, s.expires_at_ms).reason == "EXPIRED"
    assert expired([s], s.expires_at_ms) == ["s1"]
    assert evaluate(sig("price_close_above", {"level": 1}, status=Status.FIRED), bs, now_after(bs)).reason == "NOT_ACTIVE"


@pytest.mark.parametrize("has_active", [True, False])
def test_fallback_only_when_nothing_is_active(has_active):
    s = sig("price_touch", {"level": 1}, ttl=HOUR_MS)
    now = T0 + (HOUR_MS // 2 if has_active else HOUR_MS)
    fb = fallback_signal([s], now, "fb")
    if has_active:
        assert fb is None
    else:
        assert fb is not None and fb.condition == "time_check" and int(fb.params["at_ms"]) == now + 24 * HOUR_MS
        assert check_new(fb.condition, fb.params, now, fb.expires_at_ms, 0) == []
