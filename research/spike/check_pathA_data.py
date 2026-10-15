"""Data checks for the pathA-v1 book requested in the owner's review (2026-10-15).

    PYTHONPATH=<frozen/pathA-v1>/backend/src:research/spike \
        SSL_CERT_FILE=/root/.ccr/ca-bundle.crt uv run python research/spike/check_pathA_data.py

Declared before running. Signals and rules are pathA-v1's own (frozen code); nothing
is tuned. Three measurements:

1. Spot vs perpetual price. The book holds cash + BTC with spot fees and no funding,
   but its fills, marks and buy & hold are priced on USDⓈ-M BTCUSDT perpetual bars.
   Measure the daily-close basis (perp / spot - 1) on the holdout and on Oct 2026,
   and re-run the holdout with the same perp-derived weights but spot bars for fills,
   marks and buy & hold. This is reported beside the perp-priced result, which it
   does not overwrite.
2. Funding proxy. Funding for the current month is estimated from 1h premium-index
   klines. Compare the estimate with the archived actual prints for every month from
   2025-10 to 2026-09: error per print, error of the 7-day mean per day used by the
   signal, and days where the funding_hot flag would differ.
3. Decision margin. For the closes the book has decided on, how far the 7-day
   funding mean was from the threshold.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from common import jsonable

from trading.alerts import live_data, risk_check
from trading.evaluation.metrics import daily_log_returns, max_drawdown, sharpe
from trading.market_data.binance_public import _get, load, load_funding, parse_funding_csv, parse_klines_csv
from trading.strategies.funding_overlay_v3 import simulate_weights
from trading.strategies.trend_regime_v2 import daily_bars

SPOT = "https://data.binance.vision/data/spot"
UM = "https://data.binance.vision/data/futures/um"
CACHE = Path("data/pathA_checks")
OUT = Path("reports/pathA_data_checks")
EQUITY = 10_000.0
seen: dict[str, str] = {}


def fetch(url: str) -> bytes | None:
    """Archive zip verified against its published .CHECKSUM, cached on disk."""
    p = CACHE / url.split("/data/", 1)[1]
    if p.exists():
        blob = p.read_bytes()
    else:
        blob = _get(url)
        if blob is None:
            return None
        want = _get(url + ".CHECKSUM").decode().split()[0].lower()
        if hashlib.sha256(blob).hexdigest() != want:
            raise RuntimeError(f"checksum mismatch {url}")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(blob)
    seen[url] = hashlib.sha256(blob).hexdigest()
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        return z.read(z.namelist()[0])


def months(a: date, b: date):
    y, m = a.year, a.month
    while (y, m) <= (b.year, b.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def klines(base: str, symbol: str, interval: str, a: date, b: date, kind: str = "klines") -> pd.DataFrame:
    frames = []
    for y, m in months(a, b):
        raw = fetch(f"{base}/monthly/{kind}/{symbol}/{interval}/{symbol}-{interval}-{y}-{m:02d}.zip")
        if raw is None:
            d = date(y, m, 1)
            while d.month == m and d <= b:
                r = fetch(f"{base}/daily/{kind}/{symbol}/{interval}/{symbol}-{interval}-{d}.zip")
                if r is not None:
                    frames.append(parse_klines_csv(r))
                d += timedelta(days=1)
        else:
            frames.append(parse_klines_csv(raw))
    df = pd.concat(frames).sort_index()
    return df[~df.index.duplicated()]


def metrics(eq: pd.Series) -> dict:
    return {"net_return": float(eq.iloc[-1] / EQUITY - 1), "sharpe": sharpe(daily_log_returns(eq)),
            "max_dd": max_drawdown(eq)}


def spot_vs_perp(weights: pd.Series, perp_daily: pd.DataFrame, s, e) -> dict:
    spot_h1 = klines(SPOT, "BTCUSDT", "1h", s.date(), (e - pd.Timedelta(days=1)).date())
    spot_daily = daily_bars(spot_h1)
    out = {}
    for name, bars in (("perp_priced", perp_daily), ("spot_priced", spot_daily)):
        eq, tr = simulate_weights(bars, weights.fillna(1.0), s, e, equity=EQUITY)
        bh, _ = simulate_weights(bars, pd.Series(1.0, index=bars.index), s, e, equity=EQUITY)
        out[name] = {"pathA_v1": metrics(eq), "buy_and_hold": metrics(bh), "trades": int(len(tr))}
    days = perp_daily.index[(perp_daily.index >= s) & (perp_daily.index < e)]
    basis = (perp_daily["close"] / spot_daily["close"] - 1).reindex(days).dropna()
    out["basis_daily_close"] = {"days": int(len(basis)), "mean": float(basis.mean()), "median": float(basis.median()),
                                "max_abs": float(basis.abs().max()), "p95_abs": float(basis.abs().quantile(0.95))}
    return out


def recent_basis(a: date, b: date) -> dict:
    perp = klines(UM, "BTCUSDT", "1d", a, b)["close"]
    spot = klines(SPOT, "BTCUSDT", "1d", a, b)["close"]
    basis = (perp / spot - 1).dropna()
    return {"from": str(a), "to": str(b), "days": int(len(basis)), "mean": float(basis.mean()),
            "max_abs": float(basis.abs().max()),
            "by_day": {str(k.date()): round(float(v), 6) for k, v in basis.items()}}


def funding_proxy(a: date, b: date) -> dict:
    actual = pd.concat([parse_funding_csv(fetch(f"{UM}/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-{y}-{m:02d}.zip"))
                        for y, m in months(a, b)]).sort_index()
    actual.index = actual.index.round("1h")
    prem = klines(UM, "BTCUSDT", "1h", a, b, kind="premiumIndexKlines")["close"]
    est = live_data.estimate_funding(prem)
    both = pd.DataFrame({"actual": actual, "est": est}).dropna()
    err = both["est"] - both["actual"]
    daily = both.groupby(both.index.floor("1D")).sum()
    w = risk_check.FUNDING_WINDOW
    roll = daily.rolling(w, min_periods=w).mean().dropna()
    thr = risk_check.FUNDING_THRESHOLD_PER_DAY
    flips = roll[(roll["actual"] > thr) != (roll["est"] > thr)]
    return {"from": str(a), "to": str(b), "prints_compared": int(len(both)),
            "print_mae": float(err.abs().mean()), "print_max_abs": float(err.abs().max()),
            "rolling7_mae_per_day": float((roll["est"] - roll["actual"]).abs().mean()),
            "rolling7_max_abs_per_day": float((roll["est"] - roll["actual"]).abs().max()),
            "days_compared": int(len(roll)), "threshold_per_day": thr,
            "days_hot_actual": int((roll["actual"] > thr).sum()), "days_flag_differs": int(len(flips)),
            "flag_differs_on": [str(d.date()) for d in flips.index]}


def decision_margin(end: date) -> dict:
    daily = live_data.daily_klines("BTCUSDT", end - timedelta(days=400), end)
    f, est = live_data.daily_funding("BTCUSDT", end - timedelta(days=40), end)
    sig = risk_check.signals(daily, f)
    rows = sig.loc[sig.index >= pd.Timestamp(end - timedelta(days=6), tz="UTC")]
    return {"threshold_per_day": risk_check.FUNDING_THRESHOLD_PER_DAY,
            "estimated_days": [str(d.date()) for d in est[est].index],
            "rows": {str(d.date()): {"funding_7d_per_day": r["funding_7d_per_day"], "funding_hot": r["funding_hot"],
                                     "trend_up": r["trend_up"], "suggested_btc_weight": r["suggested_btc_weight"]}
                     for d, r in rows.iterrows()}}


def main() -> None:
    d = Path("data/binance_um")
    h1 = load(d, "BTCUSDT", "1h")
    perp_daily = daily_bars(h1)
    f = load_funding(d, "BTCUSDT")
    w = risk_check.signals(perp_daily, f.groupby(f.index.floor("1D")).sum())["suggested_btc_weight"]
    s, e = pd.Timestamp("2025-01-25", tz="UTC"), pd.Timestamp("2026-01-01", tz="UTC")
    report = {"signal_code": risk_check.__file__,
              "spot_vs_perp_holdout": spot_vs_perp(w, perp_daily, s, e),
              "basis_oct_2026": recent_basis(date(2026, 10, 1), date(2026, 10, 14)),
              "funding_proxy_validation": funding_proxy(date(2025, 10, 1), date(2026, 9, 30)),
              "decision_margin": decision_margin(date(2026, 10, 14))}
    report["archive_files"] = dict(sorted(seen.items()))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(jsonable(report), indent=2))
    print(json.dumps(jsonable({k: v for k, v in report.items() if k != "archive_files"}), indent=2)[:6000])


if __name__ == "__main__":
    main()
