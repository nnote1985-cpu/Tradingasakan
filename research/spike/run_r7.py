"""R7: trend long/short on BTCUSDT perpetual — pre-declared in docs/research/r7-futures-longshort-plan.md.

    PYTHONPATH=research/spike uv run python research/spike/run_r7.py development
    PYTHONPATH=research/spike uv run python research/spike/run_r7.py confirm   # C1 and C2, once, only after development passes

Downloads checksum-verified perp 1h klines and funding into data/binance_um_r7/ (cached).
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from common import jsonable

from trading.evaluation.metrics import daily_log_returns, max_drawdown, sharpe
from trading.market_data.binance_public import download, load, load_funding
from trading.strategies.trend_breakout_v1 import regularize_h1
from trading.strategies.trend_regime_v2 import daily_bars

SYMBOL = "BTCUSDT"
DATA = Path("data/binance_um_r7")
EQUITY = 10_000.0
SMA_N = 100
FEE, SLIP = 0.0005, 2.0 / 1e4
BH_FEE = 0.0010
LIQ_FRACTION = 0.10
SETS = {
    "development": ("2022-04-12", "2026-01-01"),
    "C1": ("2020-04-15", "2022-01-01"),
    "C2": ("2026-01-01", "2026-10-01"),
}


def market() -> tuple[pd.DataFrame, pd.Series, dict]:
    manifest = download(SYMBOL, date(2020, 1, 1), date(2026, 9, 30), DATA, intervals=("1h",))
    bad = [f for f in manifest["files"] if f["status"] != "VERIFIED"]
    if bad:
        raise RuntimeError(f"unverified files: {bad[:3]}")
    h1_raw = load(DATA, SYMBOL, "1h")
    d = daily_bars(h1_raw)
    h1 = regularize_h1(h1_raw)
    g = h1.groupby(h1.index.floor("1D"))
    d["high"], d["low"] = g["high"].max(), g["low"].min()
    d.loc[~d["valid"], ["high", "low"]] = np.nan
    f = load_funding(DATA, SYMBOL)
    funding = f.groupby(f.index.floor("1D")).sum()
    return d, funding, manifest


def side_state(daily: pd.DataFrame) -> pd.Series:
    """+1 LONG, -1 SHORT, 0 FLAT before SMA100 exists; invalid or equal days keep the previous side."""
    sma = daily["close"].rolling(SMA_N, min_periods=SMA_N).mean()
    raw = pd.Series(np.nan, index=daily.index)
    ok = sma.notna() & daily["close"].notna()
    raw[ok & (daily["close"] > sma)] = 1.0
    raw[ok & (daily["close"] < sma)] = -1.0
    return raw.ffill().fillna(0.0)


def simulate(daily: pd.DataFrame, funding: pd.Series, start, end) -> tuple[pd.Series, pd.DataFrame, dict]:
    days = daily.loc[(daily.index >= pd.Timestamp(start, tz="UTC")) & (daily.index < pd.Timestamp(end, tz="UTC"))]
    target = side_state(daily).shift(1).fillna(0.0).reindex(days.index)
    cash, side, qty, entry, entry_eq = EQUITY, 0, 0.0, np.nan, EQUITY
    last = np.nan
    marks, trades = [], []
    st = {"fees": 0.0, "funding_paid": 0.0, "days_long": 0, "days_short": 0, "days_flat": 0,
          "liquidated": None, "ret_long_days": 0.0, "ret_short_days": 0.0}

    def equity(px: float) -> float:
        return cash + (side * qty * (px - entry) if side else 0.0)

    prev_eq = EQUITY
    for d, row in days.iterrows():
        o, c, hi, lo = row["open"], row["close"], row["high"], row["low"]
        want = int(target.loc[d])
        if np.isfinite(o) and want != side:
            if side:
                px = o * (1 - side * SLIP)
                cash += side * qty * (px - entry)
                fee = qty * px * FEE
                cash -= fee
                st["fees"] += fee
                trades.append({"time": d, "action": "close", "side": side, "price": px, "qty": qty, "equity": cash})
                side, qty = 0, 0.0
            if want:
                px = o * (1 + want * SLIP)
                qty = cash / px
                fee = qty * px * FEE
                cash -= fee
                st["fees"] += fee
                side, entry, entry_eq = want, px, cash
                trades.append({"time": d, "action": "open", "side": side, "price": px, "qty": qty, "equity": cash})
        if side:
            worst = hi if side < 0 else lo
            if np.isfinite(worst) and equity(worst) <= LIQ_FRACTION * entry_eq:
                st["liquidated"] = str(d.date())
                cash, side, qty = equity(worst), 0, 0.0
                marks.append(cash)
                break
        if np.isfinite(c):
            last = c
        if side and d in funding.index and np.isfinite(last):
            pay = side * qty * last * float(funding.loc[d])
            cash -= pay
            st["funding_paid"] += pay
        eq = equity(last) if np.isfinite(last) else prev_eq
        r = eq / prev_eq - 1
        if side > 0:
            st["days_long"] += 1
            st["ret_long_days"] += float(np.log1p(r))
        elif side < 0:
            st["days_short"] += 1
            st["ret_short_days"] += float(np.log1p(r))
        else:
            st["days_flat"] += 1
        marks.append(eq)
        prev_eq = eq
    idx = days.index[: len(marks)]
    eq = pd.concat([pd.Series([EQUITY], index=[idx[0] - pd.Timedelta(days=1)]), pd.Series(marks, index=idx)])
    return eq, pd.DataFrame(trades), st


def buy_and_hold(daily: pd.DataFrame, start, end) -> pd.Series:
    days = daily.loc[(daily.index >= pd.Timestamp(start, tz="UTC")) & (daily.index < pd.Timestamp(end, tz="UTC"))]
    px = days["open"].dropna().iloc[0] * (1 + SLIP)
    qty = EQUITY * (1 - BH_FEE) / px
    eq = (qty * days["close"]).ffill()
    return pd.concat([pd.Series([EQUITY], index=[days.index[0] - pd.Timedelta(days=1)]), eq])


def run_set(name: str, daily, funding) -> tuple[dict, pd.Series, pd.Series, pd.DataFrame]:
    s, e = SETS[name]
    eq, trades, st = simulate(daily, funding, s, e)
    bh = buy_and_hold(daily, s, e)
    ruined = (eq <= 0).any()
    out = {
        "set": name, "range": SETS[name],
        "net": float(eq.iloc[-1] / EQUITY - 1), "sharpe": float("nan") if ruined else sharpe(daily_log_returns(eq)),
        "max_dd": max_drawdown(eq),
        "bh_net": float(bh.iloc[-1] / EQUITY - 1), "bh_sharpe": sharpe(daily_log_returns(bh)), "bh_max_dd": max_drawdown(bh),
        "flips": int((trades["action"] == "open").sum()) if len(trades) else 0, **st,
    }
    out["passes"] = bool(out["net"] > 0 and out["sharpe"] > out["bh_sharpe"])
    return out, eq, bh, trades


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["development", "confirm"])
    ap.add_argument("--out", default="/mnt/project-files/r7-results")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.stage == "confirm":
        dev = out / "development.json"
        if not dev.exists() or not all(r["passes"] for r in json.loads(dev.read_text())["runs"]):
            raise SystemExit("development has not passed; C1 and C2 stay unspent")
        if (out / "confirmation.json").exists():
            raise SystemExit("confirmation sets were already spent; see confirmation.json")
    daily, funding, manifest = market()
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    sets = ["development"] if a.stage == "development" else ["C1", "C2"]
    report = {"commit": commit, "stage": a.stage, "files": len(manifest["files"]),
              "coverage": {k: v for k, v in manifest.items() if k.startswith("coverage")}, "runs": []}
    for s in sets:
        r, eq, bh, trades = run_set(s, daily, funding)
        report["runs"].append(r)
        trades.to_csv(out / f"trades_{s}.csv", index=False)
        pd.DataFrame({"strategy": eq, "buy_and_hold": bh}).to_csv(out / f"equity_{s}.csv")
    (out / ("development.json" if a.stage == "development" else "confirmation.json")).write_text(json.dumps(jsonable(report), indent=2))
    for r in report["runs"]:
        print(f"R7 {r['set']}: net {r['net']:+.1%} sharpe {r['sharpe']:.2f} dd {r['max_dd']:.1%} | "
              f"B&H {r['bh_net']:+.1%} {r['bh_sharpe']:.2f} {r['bh_max_dd']:.1%} | flips {r['flips']} "
              f"long {r['days_long']}d short {r['days_short']}d flat {r['days_flat']}d | fees {r['fees']:.0f} "
              f"funding {r['funding_paid']:+.0f} | long-days {r['ret_long_days']:+.3f} short-days {r['ret_short_days']:+.3f} "
              f"liq {r['liquidated']} -> {'PASS' if r['passes'] else 'fail'}")


if __name__ == "__main__":
    np.seterr(all="ignore")
    main()
