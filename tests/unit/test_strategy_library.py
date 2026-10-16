import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from trading.strategies.library import catalog
from trading.strategies.library import profile as bp
from trading.strategies.library.bars import resample, rsi_wilder

T0 = pd.Timestamp("2023-01-01", tz="UTC")


def _h1(n_hours: int, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    c = 20_000 * np.exp(np.cumsum(rng.normal(0, 0.01, n_hours)))
    idx = pd.date_range(T0, periods=n_hours, freq="1h", tz="UTC")
    return pd.DataFrame({"open": c, "high": c * 1.002, "low": c * 0.998, "close": c}, index=idx)


H1 = _h1(24 * 400)


def _profile(**kw) -> bp.BotProfile:
    body = {"profile_id": "t-1", "version": 1, "name": "t", "strategies": [{"number": 1}]} | kw
    return bp.BotProfile.model_validate(body)


def test_numbers_are_unique_and_match_keys():
    assert all(k == s.no for k, s in catalog.CATALOG.items())
    assert len({s.template_id for s in catalog.CATALOG.values()}) == len(catalog.CATALOG)


@pytest.mark.parametrize("no", sorted(catalog.CATALOG))
@pytest.mark.parametrize("tf", ["4h", "1d"])
def test_every_strategy_gives_allowed_weights_without_lookahead(no, tf):
    spec = catalog.get(no)
    full = spec.signal(resample(H1, tf), timeframe=tf)
    assert set(full.unique()) <= {0.0, 0.5, 1.0}
    cut = H1.index[len(H1) * 2 // 3].floor("1D")
    part = spec.signal(resample(H1[H1.index < cut], tf), timeframe=tf)
    pd.testing.assert_series_equal(full.loc[part.index], part)


def test_signal_is_indexed_by_bar_close_time():
    w = catalog.get(7).signal(resample(H1, "1d"), {"sma": 10}, "1d")
    assert w.index[0] == T0 + pd.Timedelta(days=1)


def test_invalid_bar_holds_the_previous_weight():
    bars = resample(H1.drop(H1.index[24 * 50 + 3]), "1d")
    assert not bars["valid"].iloc[50]
    w = catalog.get(7).signal(bars, {"sma": 10}, "1d")
    assert w.iloc[50] == w.iloc[49]


def test_resample_needs_every_hour():
    d = resample(H1.drop(H1.index[5]), "4h")
    assert d["valid"].tolist()[:3] == [True, False, True]


def test_rsi_is_100_on_a_rising_series():
    assert rsi_wilder(np.arange(30, dtype=float), 14)[-1] == 100.0


def test_params_outside_bounds_or_unknown_are_rejected():
    with pytest.raises(ValueError, match="outside"):
        catalog.get(1).resolve({"fast": 99})
    with pytest.raises(ValueError, match="no params"):
        catalog.get(1).resolve({"nope": 1})
    with pytest.raises(ValueError, match="integer"):
        catalog.get(7).resolve({"sma": 100.5})


def test_profile_validation():
    with pytest.raises(ValidationError, match="no strategy number"):
        _profile(strategies=[{"number": 999}])
    with pytest.raises(ValidationError, match="twice"):
        _profile(strategies=[{"number": 1}, {"number": 1}])
    with pytest.raises(ValidationError, match="does not run on"):
        _profile(strategies=[{"number": 1, "timeframe": "15m"}])
    with pytest.raises(ValidationError, match="spot.v1"):
        _profile(risk_profile="futures.default")
    with pytest.raises(ValidationError):
        _profile(leverage=3)
    with pytest.raises(ValidationError):
        _profile(ai_mode="bounded_template")


def test_swapping_strategy_numbers_changes_the_hash():
    a = _profile(strategies=[{"number": 1}, {"number": 2}, {"number": 3}])
    b = _profile(strategies=[{"number": 2}, {"number": 4}, {"number": 6}])
    assert a.profile_hash() != b.profile_hash()
    assert a.profile_hash() == _profile(strategies=[{"number": 1}, {"number": 2}, {"number": 3}]).profile_hash()
    # spelling out a default param is the same profile
    assert _profile(strategies=[{"number": 7}]).profile_hash() == _profile(strategies=[{"number": 7, "params": {"sma": 100}}]).profile_hash()


@pytest.mark.parametrize("ws,avg,mwf", [
    ([1, 1, 1], 1.0, 1.0), ([1, 0.5, 1], 0.5, 1.0), ([1, 0, 1], 0.5, 0.0),
    ([0, 1, 1], 0.5, 0.0), ([1, 0, 0], 0.0, 0.0), ([0.5, 1], 0.5, 0.5), ([1], 1.0, 1.0),
])
def test_combine(ws, avg, mwf):
    assert bp.combine(ws, "average") == avg
    assert bp.combine(ws, "main_with_filters") == mwf


def test_daily_weights_combine_per_day():
    p = _profile(strategies=[{"number": 1, "timeframe": "4h"}, {"number": 7, "timeframe": "1d"}], combine="main_with_filters")
    out = bp.daily_weights(p, H1)
    assert list(out.columns) == ["s1", "s7", "target_weight"]
    assert out.index[0] == T0 + pd.Timedelta(days=1)
    expect = [bp.combine([a, b], "main_with_filters") for a, b in zip(out["s1"], out["s7"])]
    assert out["target_weight"].tolist() == expect


def test_repo_profiles_load():
    profiles = bp.load_all()
    assert {p.profile_id for p in profiles} >= {"btc-trend-mix", "btc-macd-rsi"}


def test_api_views_list_catalog_and_profiles():
    from api.strategies import catalog_view, profiles_view

    nums = [s["number"] for s in catalog_view()["items"]]
    assert nums == sorted(catalog.CATALOG)
    prof = {p["profile_id"]: p for p in profiles_view()["items"]}
    assert [s["number"] for s in prof["btc-macd-rsi"]["strategies"]] == [1, 7]
    assert prof["btc-macd-rsi"]["strategies"][0]["params"] == {"fast": 12, "slow": 26, "signal": 9}
