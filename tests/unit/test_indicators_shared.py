"""M2 PR 2 (docs/m2-charts-plan.md §5.1 I2, I3): indicators moved into trading.features.indicators
return the same floats bit for bit as the code they replaced, copied below from main 41b468d."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trading.features import indicators as ind
from trading.strategies import argus_e1, sma_band

# --- the replaced code, verbatim ------------------------------------------------------------


def _old_ema(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).ewm(alpha=2.0 / (n + 1), adjust=False).mean().to_numpy(copy=True)


def _old_adx(h: np.ndarray, lo: np.ndarray, c: np.ndarray, n: int) -> np.ndarray:
    ph, pl, pc = np.r_[np.nan, h[:-1]], np.r_[np.nan, lo[:-1]], np.r_[np.nan, c[:-1]]
    pdm, ndm = h - ph, pl - lo
    pdm, ndm = np.where(pdm < 0, 0.0, pdm), np.where(ndm < 0, 0.0, ndm)
    keep_p, keep_n = pdm > ndm, ndm > pdm
    pdm, ndm = np.where(keep_p, pdm, 0.0), np.where(keep_n, ndm, 0.0)
    tr = np.fmax(h, pc) - np.fmin(lo, pc)
    with np.errstate(invalid="ignore", divide="ignore"):
        p_raw = np.where(tr > 0, 100.0 * pdm / tr, 0.0)
        n_raw = np.where(tr > 0, 100.0 * ndm / tr, 0.0)
    p_raw[0] = n_raw[0] = 0.0
    pdi, mdi = _old_ema(p_raw, n), _old_ema(n_raw, n)
    with np.errstate(invalid="ignore", divide="ignore"):
        dx = np.where(pdi + mdi > 0, 100.0 * np.abs(pdi - mdi) / (pdi + mdi), 0.0)
    out = _old_ema(dx, n)
    out[: 2 * n] = np.nan
    return out


def _old_band_state(close: pd.Series, *, signed: bool, sma_n: int = 100, band: float = 0.02) -> pd.Series:
    c = close.astype(float).to_numpy()
    s = close.astype(float).rolling(sma_n, min_periods=sma_n).mean().to_numpy()
    out = np.full(len(c), np.nan if not signed else 0.0)
    state: float | None = None
    lo = -1.0 if signed else 0.0
    for i in range(len(c)):
        ok = np.isfinite(c[i]) and np.isfinite(s[i])
        if ok:
            if state is None:
                state = 1.0 if c[i] > s[i] else (lo if c[i] < s[i] or not signed else 0.0)
            elif state != 1.0 and c[i] > s[i] * (1 + band):
                state = 1.0
            elif state != lo and c[i] < s[i] * (1 - band):
                state = lo
        if signed:
            out[i] = state or 0.0
        elif ok:
            out[i] = state
    return pd.Series(out, index=close.index)


def _old_risk_check_sma(close: pd.Series) -> np.ndarray:
    """alerts.risk_check.signals: c.rolling(100, min_periods=100).mean() on the reindexed days."""
    return close.astype(float).rolling(100, min_periods=100).mean().to_numpy()


# --- data -----------------------------------------------------------------------------------


def _bars(seed: int, n: int = 900, gaps: bool = False) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    c = 60_000 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    spread = np.abs(rng.normal(0, 0.01, n)) * c
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) + spread
    lo = np.minimum(o, c) - spread
    df = pd.DataFrame({"open": o, "high": h, "low": lo, "close": c},
                      index=pd.date_range("2023-01-01", periods=n, freq="1D", tz="UTC"))
    if gaps:
        for at in rng.choice(np.arange(5, n - 5), size=12, replace=False):
            df.iloc[at : at + int(rng.integers(1, 4))] = np.nan
    return df


SEEDS = [1, 2, 3, 7, 42]


def _same(a: np.ndarray, b: np.ndarray) -> None:
    assert np.array_equal(np.asarray(a), np.asarray(b), equal_nan=True)


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("gaps", [False, True])
def test_band_latch_bit_identical(seed: int, gaps: bool) -> None:
    close = _bars(seed, gaps=gaps)["close"]
    for signed in (False, True):
        _same(sma_band.band_state(close, signed=signed), _old_band_state(close, signed=signed))
        _same(ind.band_latch(close, signed=signed, sma_n=100, band=0.02), _old_band_state(close, signed=signed))


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("gaps", [False, True])
def test_sma_equals_risk_check_rolling(seed: int, gaps: bool) -> None:
    close = _bars(seed, gaps=gaps)["close"]
    _same(ind.sma(close, 100), _old_risk_check_sma(close))
    _same(ind.sma(close.to_numpy(), 100), _old_risk_check_sma(close))


@pytest.mark.parametrize("seed", SEEDS)
def test_e1_indicators_bit_identical(seed: int) -> None:
    b = _bars(seed)
    h, lo, c = (b[k].to_numpy(float) for k in ("high", "low", "close"))
    for n in (20, 50, 200):
        _same(ind.ema_mt5(c, n), _old_ema(c, n))
        _same(argus_e1.ema(c, n), _old_ema(c, n))
    _same(ind.adx_mt5(h, lo, c, 14), _old_adx(h, lo, c, 14))
    _same(argus_e1.adx(h, lo, c, 14), _old_adx(h, lo, c, 14))
    up, dn = ind.donchian(h, lo, 20)
    _same(up, pd.Series(h).rolling(20, min_periods=20).max().to_numpy())
    _same(dn, pd.Series(lo).rolling(20, min_periods=20).min().to_numpy())
    sig = argus_e1.E1Signals(b)
    _same(sig.hi20, up)
    _same(sig.lo20, dn)
    _same(sig.e50, _old_ema(c, 50))
    _same(sig.e200, _old_ema(c, 200))
    _same(sig.adx, _old_adx(h, lo, c, 14))


def test_donchian_shift_is_previous_high_low() -> None:
    b = _bars(5)
    h, lo = b["high"].to_numpy(float), b["low"].to_numpy(float)
    up, dn = ind.donchian(h, lo, 20)
    _same(ind.previous_high(h, 20)[1:], up[:-1])
    _same(ind.previous_low(lo, 20)[1:], dn[:-1])


def test_two_ema_conventions_differ_and_are_named() -> None:
    c = _bars(9)["close"].to_numpy(float)
    assert ind.ema_sma_seed is ind.ema
    a, b = ind.ema_sma_seed(c, 20), ind.ema_mt5(c, 20)
    assert np.isnan(a[18]) and not np.isnan(b[18])
    assert abs(a[19] - b[19]) > 0
