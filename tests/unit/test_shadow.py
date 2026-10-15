"""Phase 3 acceptance tests (docs/phase-3-research-mvp.md)."""

import json
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from trading.risk.sizing import RiskProfile, SizingAllowances
from trading.shadow.ledger import ShadowLedger
from trading.shadow.portfolio import ShadowConfig, ShadowPortfolio
from trading.shadow.risk_state import RiskState
from trading.simulator.bar_backtest import BacktestConfig, BarBacktester, CostModel

T0 = pd.Timestamp("2024-03-01 00:00", tz="UTC")
NO_SLIP = CostModel(taker_fee=0.0005, entry_slip_bps=0, exit_slip_bps=0, stop_slip_bps=0)
NO_FUND_RESERVE = SizingAllowances(funding_reserve_fraction=Decimal("0"))
ZERO_DELAY = dict(decision_delay=pd.Timedelta(0), network_delay=pd.Timedelta(0))
FLAT = (60000, 60100, 59900, 60000)


def frames(bars, signals, freq="1h", signal_freq="1h", atr=300.0):
    idx = pd.date_range(T0, periods=len(bars), freq=freq, tz="UTC")
    ex = pd.DataFrame(bars, columns=["open", "high", "low", "close"], index=idx, dtype=float)
    sidx = pd.date_range(T0, periods=max(signals, default=0) + 2, freq=signal_freq, tz="UTC")
    h1 = pd.DataFrame({"signal": "NO_TRADE", "close_time": sidx + pd.Timedelta(signal_freq), "atr": atr}, index=sidx)
    for k, side in signals.items():
        h1.iloc[k, h1.columns.get_loc("signal")] = side
    h4 = pd.DataFrame(columns=["close_time", "close", "ema_slow"])
    return ex, h1, h4


def shadow(**kw):
    return ShadowPortfolio(ShadowConfig(costs=kw.pop("costs", NO_SLIP), allowances=kw.pop("allowances", NO_FUND_RESERVE), **kw))


# ---- ledger ---------------------------------------------------------------

def test_T15_worked_example_economic_net_and_no_double_slippage():
    led = ShadowLedger()
    led.open("open", T0, 10000)
    led.fill("e1", T0, "BUY", "0.069", "60000", Decimal("60000") * Decimal("0.069") * Decimal("0.0005"), reference_price="59990")
    led.funding("f1", T0, Decimal("0.5") / (Decimal("0.069") * Decimal("61000")), "61000")
    led.fill("e2", T0, "SELL", "0.069", "61200", Decimal("61200") * Decimal("0.069") * Decimal("0.0005"))
    led.operating_cost("ai1", T0, "0.03")
    assert led.trading_equity(61200) - 10000 == Decimal("78.1186")
    assert led.economic_equity(61200) - 10000 == Decimal("78.0886")
    assert Decimal(led.entries[1].data["slippage"]) == Decimal("0.690")      # recorded, not deducted
    assert led.check() == []


def test_T16_duplicate_execution_and_funding_change_nothing():
    led = ShadowLedger()
    led.open("open", T0, 10000)
    assert led.fill("x1", T0, "BUY", "0.1", "60000", "3")
    assert led.funding("f1", T0, "0.0001", "60000")
    snap = (led.bal("cash"), led.position.qty, len(led.entries))
    assert not led.fill("x1", T0, "BUY", "0.1", "60000", "3")
    assert not led.funding("f1", T0, "0.0001", "60000")
    assert (led.bal("cash"), led.position.qty, len(led.entries)) == snap


def test_partial_close_realises_only_closed_quantity():
    led = ShadowLedger()
    led.open("open", T0, 10000)
    led.fill("a", T0, "BUY", "0.1", "60000", "0")
    led.fill("b", T0, "BUY", "0.1", "62000", "0")
    assert led.position.avg_entry == Decimal("61000")
    led.fill("c", T0, "SELL", "0.05", "63000", "1")
    assert led.bal("realized_pnl") == Decimal("-100")                       # credit: profit 0.05 * 2000
    assert led.position.qty == Decimal("0.15") and led.position.avg_entry == Decimal("61000")
    assert led.trading_equity(61000) == Decimal("10099")
    assert led.check() == []


def test_ledger_round_trips_through_json():
    led = ShadowLedger()
    led.open("open", T0, 10000)
    led.fill("a", T0, "SELL", "0.2", "60000", "6")
    again = ShadowLedger.from_dict(json.loads(json.dumps(led.to_dict())))
    assert again.balances == led.balances and again.position == led.position and again.check() == []


def test_T38_costs_continue_after_halt():
    led = ShadowLedger()
    led.open("open", T0, 9180)
    for d in range(20):
        led.operating_cost(f"c{d}", T0, "0.20")
    assert led.trading_equity(1) == Decimal("9180")
    assert led.economic_equity(1) == Decimal("9176")


# ---- risk state -----------------------------------------------------------

def test_T22_halt_survives_midnight_and_restart():
    rs = RiskState()
    p = RiskProfile()
    rs.start_day(T0, Decimal(10000), Decimal(10000))
    assert rs.observe(T0, Decimal(9790), Decimal(9790), p) == "DAILY_LOSS_TRADING"
    rs = RiskState.from_dict(json.loads(json.dumps(rs.to_dict())))
    nxt = T0 + pd.Timedelta(days=1)
    assert rs.start_day(nxt, Decimal(9790), Decimal(9790))
    assert rs.observe(nxt, Decimal(9800), Decimal(9800), p) is None
    assert rs.halted and rs.halt_reason == "DAILY_LOSS_TRADING"
    assert rs.remaining_day_budget(Decimal(9800), Decimal(0), p) == 0


def test_daily_loss_on_economic_equity_and_drawdown_on_hwm():
    p = RiskProfile()
    rs = RiskState()
    rs.start_day(T0, Decimal(10000), Decimal(10000))
    assert rs.observe(T0, Decimal(9900), Decimal(9799), p) == "DAILY_LOSS_ECONOMIC"
    rs = RiskState()
    for d, eq in enumerate([10000, 10500, 10300, 10100, 9900, 9650]):
        t = T0 + pd.Timedelta(days=d)
        rs.start_day(t, Decimal(eq), Decimal(eq))
        r = rs.observe(t, Decimal(eq), Decimal(eq), p)
    assert r == "DRAWDOWN" and rs.high_water_mark == Decimal(10500)       # 9650 <= 10500 * 0.92 = 9660


def test_remaining_budget_subtracts_open_loss_to_stop():
    rs = RiskState()
    p = RiskProfile()
    rs.start_day(T0, Decimal(10000), Decimal(10000))
    assert rs.remaining_day_budget(Decimal(9950), Decimal(40), p) == Decimal(110)   # 200 - 50 - 40


# ---- portfolio ------------------------------------------------------------

def test_fill_timing_never_before_eligible_time():
    # 1m execution bars; v1 signal on the 10:00 1h close (bar index 9 of the hourly frame).
    bars = [FLAT] * (11 * 60)
    ex, h1, h4 = frames(bars, {9: "LONG"}, freq="1min")
    book = shadow(decision_delay=pd.Timedelta(seconds=1))
    book.run(ex, h1, h4)
    c = book.candidates[0]
    assert c["decision_at"] == "2024-03-01T10:00:01+00:00"
    assert c["eligible_fill_at"] == "2024-03-01T10:00:01.100000+00:00"
    assert c["entry_bar"] == "2024-03-01T10:01:00+00:00" and c["status"] == "OPENED"
    assert pd.Timestamp(book.pos["entry_time"]) >= pd.Timestamp(c["eligible_fill_at"])
    # With no delay the same signal could fill at the 10:00 open.
    zero = shadow(**ZERO_DELAY)
    zero.run(ex, h1, h4)
    assert zero.candidates[0]["entry_bar"] == "2024-03-01T10:00:00+00:00"


def test_T13_same_bar_sl_and_tp_is_conservative_and_labelled():
    ex, h1, h4 = frames([FLAT, FLAT, (60000, 61300, 59300, 60000), FLAT], {0: "LONG"})
    book = shadow(**ZERO_DELAY)
    book.run(ex, h1, h4)
    tr = book.trades[0]
    assert tr["exit_reason"] == "STOP" and tr["ambiguous_bar"] is True and Decimal(tr["exit_price"]) == 59400


def test_T14_gap_through_stop_fills_at_open():
    ex, h1, h4 = frames([FLAT, FLAT, (58900, 59000, 58800, 58900), FLAT], {0: "LONG"})
    book = shadow(**ZERO_DELAY)
    book.run(ex, h1, h4)
    tr = book.trades[0]
    assert tr["exit_reason"] == "STOP_GAP" and Decimal(tr["exit_price"]) == 58900


def test_take_profit_from_actual_fill():
    slip = CostModel(taker_fee=0.0005, entry_slip_bps=2, exit_slip_bps=0, stop_slip_bps=0)
    ex, h1, h4 = frames([FLAT, FLAT, FLAT], {0: "LONG"})
    book = shadow(costs=slip, **ZERO_DELAY)
    book.run(ex, h1, h4)
    p = book.pos
    fill, stop, tp = Decimal(p["entry_price"]), Decimal(p["stop"]), Decimal(p["take_profit"])
    assert fill == Decimal("60012.0") and tp <= fill + 2 * (fill - stop) and tp > fill + 2 * (fill - stop) - Decimal("0.1")


def test_T31_halt_flattens_blocks_entries_and_equity_runs_to_endpoint():
    # Day 1's allocated cost (900) is booked when the day ends: economic equity falls 9% below
    # the high-water mark while the position is open, so the book halts and flattens next bar.
    bars = [FLAT] * 72
    ex, h1, h4 = frames(bars, {0: "LONG", 30: "LONG"})
    book = shadow(daily_operating_cost=Decimal("900"), **ZERO_DELAY)
    book.run(ex, h1, h4)
    assert book.risk.halted and book.risk.halt_reason == "DRAWDOWN"
    assert book.risk.halted_at == "2024-03-02T00:00:00+00:00"
    assert book.trades[0]["exit_reason"] == "HALT_FLATTEN"
    assert book.trades[0]["exit_time"] == "2024-03-02T01:00:00+00:00"
    assert [c["status"] for c in book.candidates] == ["OPENED", "NOT_EVALUATED_HALTED"]
    assert book.last_bar == ex.index[-1].isoformat()
    assert book.ledger.bal("operating_costs") == Decimal("1800")            # costs keep accruing after the halt
    tr, ec = book.equity()
    assert ec == tr - Decimal("1800")


def test_T31_gap_loss_halts_on_daily_limit():
    ex, h1, h4 = frames([FLAT, FLAT, (40000, 40000, 40000, 40000)] + [FLAT] * 5, {0: "LONG", 4: "LONG"})
    book = shadow(**ZERO_DELAY)
    book.run(ex, h1, h4)
    assert book.trades[0]["exit_reason"] == "STOP_GAP"
    assert book.risk.halt_reason == "DAILY_LOSS_TRADING"
    assert book.candidates[1]["status"] == "NOT_EVALUATED_HALTED"


def test_save_load_equals_unbroken_run():
    rng = np.random.default_rng(7)
    ex, h1, h4 = _walk(rng, 24 * 20)
    whole = shadow(**ZERO_DELAY)
    whole.run(ex, h1, h4)
    a = shadow(**ZERO_DELAY)
    cut = ex.index[len(ex) // 2]
    a.run(ex[ex.index <= cut], h1, h4)
    b = ShadowPortfolio(a.cfg, json.loads(json.dumps(a.to_dict())))
    b.run(ex, h1, h4)
    assert b.trades == whole.trades and b.candidates == whole.candidates
    assert b.equity() == whole.equity() and b.ledger.check() == []


def test_loading_with_another_config_is_refused():
    book = shadow(**ZERO_DELAY)
    with pytest.raises(ValueError):
        ShadowPortfolio(ShadowConfig(), book.to_dict())


def _walk(rng, n):
    steps = rng.normal(0, 250, n).cumsum() + 60000
    o = np.r_[60000, steps[:-1]]
    c = steps
    h = np.maximum(o, c) + rng.uniform(0, 300, n)
    lo = np.minimum(o, c) - rng.uniform(0, 300, n)
    sig = {int(k): ("LONG" if rng.random() < 0.5 else "SHORT") for k in rng.choice(n - 2, n // 12, replace=False)}
    return frames(list(zip(o, h, lo, c)), sig)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_exact_v1_matches_bar_backtest(seed):
    """Same candidates, same bars, zero decision delay: same trades as the research simulator."""
    rng = np.random.default_rng(seed)
    ex, h1, h4 = _walk(rng, 24 * 30)
    fund = pd.Series(0.0001, index=pd.date_range(T0, ex.index[-1], freq="8h", tz="UTC"))
    costs = CostModel()
    ref = BarBacktester(ex, h1, h4, fund, BacktestConfig(costs=costs)).run()
    book = ShadowPortfolio(ShadowConfig(costs=costs, **ZERO_DELAY))
    book.run(ex, h1, h4, fund)
    ours = pd.DataFrame(book.trades)
    want = ref.trades[ref.trades["exit_reason"] != "END_OF_DATA"].reset_index(drop=True)
    assert len(ours) == len(want) and len(ours) > 5
    for col in ("entry_time", "exit_time"):
        assert [pd.Timestamp(x) for x in ours[col]] == list(want[col])
    assert list(ours["exit_reason"]) == list(want["exit_reason"])
    assert np.allclose(ours["qty"].astype(float), want["qty"])
    assert np.allclose(ours["net_pnl"].astype(float), want["net_pnl"], atol=1e-6)
    statuses = [c["status"] for c in book.candidates]
    assert statuses == list(ref.candidates["status"][: len(statuses)])
