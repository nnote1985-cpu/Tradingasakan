"""R17: short-specific sleeve beside R10a with exchange-side SL/TP, 1% risk sizing and loss caps,
pre-declared in docs/research/r17-short-sleeve-plan.md (commit a923448). Run once per window.

    PYTHONPATH=research/spike uv run python research/spike/run_r17.py dev
    PYTHONPATH=research/spike uv run python research/spike/run_r17.py c1    # once, after dev is committed

The sleeve trades only while R10a holds cash, so account equity = R10a equity x sleeve growth factor G
(R10a re-enters with whatever cash the sleeve left). No 2026 data is loaded.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import run_r15
from common import jsonable

from trading.evaluation.metrics import daily_log_returns, max_drawdown, sharpe
from trading.market_data.binance_public import load, load_funding
from trading.strategies.funding_overlay_v3 import simulate_weights
from trading.strategies.trend_breakout_v1 import regularize_h1
from trading.strategies.trend_regime_v2 import RegimeCosts

FEE, SLIP, MAINT = 0.0005, 2.0 / 1e4, 0.005
RISK, LEV_CAP, DAY_CAP, HALT = 0.01, 1.0, 0.03, 0.10
SL_ATR, TP_ATR, MAX_DAYS = 1.5, 2.0, 10
WINDOWS = {"dev": ("2022-08-01", "2026-01-01"), "c1": ("2020-08-01", "2022-01-01")}


def daily_features(daily: pd.DataFrame, feats: pd.DataFrame) -> pd.DataFrame:
    c, h, lo = daily["close"].ffill(), daily["high"].ffill(), daily["low"].ffill()
    tr = pd.concat([h - lo, (h - c.shift()).abs(), (lo - c.shift()).abs()], axis=1).max(axis=1)
    sma200 = c.rolling(200, min_periods=200).mean()
    out = pd.DataFrame({
        "close": daily["close"], "high": daily["high"],
        "sma200": sma200, "sma200_falling": sma200 < sma200.shift(20),
        "ema20": c.ewm(span=20, adjust=False, min_periods=20).mean(),
        "atr14": tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean(),
        "low10_prev": c.rolling(10).min().shift(1),
        "r10a_in": feats["band"].reindex(daily.index) == 1,
    })
    out["regime"] = (out["close"] < out["sma200"]) & out["sma200_falling"] & ~out["r10a_in"]
    out["entry"] = out["regime"] & (((out["high"] >= out["ema20"]) & (out["close"] < out["ema20"]))
                                    | (out["close"] < out["low10_prev"]))
    return out


def sleeve(h1: pd.DataFrame, fund: pd.Series, df: pd.DataFrame, start, end, fee=FEE, slip=SLIP):
    bars = h1.loc[(h1.index >= pd.Timestamp(start, tz="UTC")) & (h1.index < pd.Timestamp(end, tz="UTC"))]
    g_cash, qty, entry, sl, tp, t_in, n_frac = 1.0, 0.0, np.nan, np.nan, np.nan, None, 0.0
    peak, halted, day_start_g = 1.0, None, 1.0
    pending_entry, pending_exit = None, False
    trades, marks = [], {}
    st = {"fees": 0.0, "funding_received": 0.0, "daily_cap_blocks": 0, "halted": None}
    last_c = np.nan

    def close(px_raw, t, why):
        nonlocal g_cash, qty
        px = px_raw * (1 + slip)                       # buy to cover
        f = qty * px * fee
        pnl = qty * (entry - px) - f
        g_cash += pnl
        st["fees"] += f
        trades[-1].update({"exit_time": str(t), "exit": px, "why": why, "pnl_frac_of_equity": pnl / trades[-1]["g_at_entry"],
                           "days": (t - t_in) / pd.Timedelta(days=1)})
        qty = 0.0

    for t, row in bars.iterrows():
        o, hi, lo, c = row["open"], row["high"], row["low"], row["close"]
        day = t.tz_localize(None).floor("1D")
        if t.hour == 0:
            day_start_g = g_cash + (qty * (entry - last_c) if qty else 0.0)
        if not np.isfinite(o):
            continue
        if qty and t in fund.index:                     # short receives positive funding
            rec = qty * o * float(fund.loc[t])
            g_cash += rec
            st["funding_received"] += rec
        if t.hour == 0 and pending_exit and qty:
            close(o, t, "regime_or_time")
        pending_exit = False
        if t.hour == 0 and pending_entry is not None and not qty and halted is None:
            atr = pending_entry
            eq_now = g_cash
            if eq_now < day_start_g * (1 - DAY_CAP):
                st["daily_cap_blocks"] += 1
            else:
                px = o * (1 - slip)
                sl_dist = SL_ATR * atr
                n_frac = min(RISK / (sl_dist / px), LEV_CAP)
                qty = n_frac * eq_now / px
                f = qty * px * fee
                g_cash -= f
                st["fees"] += f
                entry, sl, tp, t_in = px, px + sl_dist, px - TP_ATR * atr, t
                liq = entry * (1 + (eq_now / (qty * entry)) - MAINT)   # isolated, margin = equity share at 1x cap
                liq_iso = entry * (1 + 1 / LEV_CAP - MAINT)
                trades.append({"entry_time": str(t), "entry": px, "sl": sl, "tp": tp, "notional_frac": n_frac,
                               "sl_dist_pct": sl_dist / px * 100, "liq_dist_isolated_1x_pct": (liq_iso / entry - 1) * 100,
                               "liq_dist_cross_account_pct": (liq / entry - 1) * 100, "g_at_entry": eq_now})
        pending_entry = None
        if qty:
            # exchange-side orders: SL first if both are touched in the same hour
            if hi >= sl:
                close(max(o, sl), t, "SL")
            elif lo <= tp:
                close(min(o, tp), t, "TP")
        if np.isfinite(c):
            last_c = c
        g_mark = g_cash + (qty * (entry - last_c) if qty else 0.0)
        peak = max(peak, g_mark)
        if halted is None and peak - g_mark >= HALT:
            halted = str(t)
            st["halted"] = halted
            if qty:
                close(c, t, "HALT")
                g_mark = g_cash
        if t.hour == 23:
            marks[day] = g_mark
            x = df.loc[day] if day in df.index else None
            if x is not None:
                if qty:
                    held = (t + pd.Timedelta(hours=1) - t_in) / pd.Timedelta(days=1)
                    if (not bool(x["regime"])) or held >= MAX_DAYS:
                        pending_exit = True
                elif bool(x["entry"]) and np.isfinite(x["atr14"]):
                    pending_entry = float(x["atr14"])
    if qty:
        close(last_c, bars.index[-1], "end")
        marks[max(marks)] = g_cash
    g = pd.Series(marks).sort_index()
    return g, pd.DataFrame(trades), st


def m(eq: pd.Series) -> dict:
    return {"net": float(eq.iloc[-1] / eq.iloc[0] - 1), "sharpe": sharpe(daily_log_returns(eq)), "max_dd": max_drawdown(eq)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("window", choices=list(WINDOWS))
    ap.add_argument("--out", default="/mnt/project-files/r17-results")
    a = ap.parse_args()
    start, end = WINDOWS[a.window]
    win = run_r15.WINDOWS[a.window]
    out = Path(a.out) / a.window
    if a.window == "c1":
        if not (Path(a.out) / "dev" / "report.json").exists():
            raise SystemExit("run dev first")
        if (out / "report.json").exists():
            raise SystemExit("C1 already run for R17")
    out.mkdir(parents=True, exist_ok=True)
    np.seterr(all="ignore")
    daily, spot, fd, _ = run_r15.market(win)
    _, feats = run_r15.targets(daily, fd)
    df = daily_features(daily, feats)
    raw = load(Path(win["perp_dir"]), "BTCUSDT", "1h")
    if raw.index.max() >= pd.Timestamp("2026-01-01", tz="UTC"):
        raise RuntimeError("C2 guard")
    h1 = regularize_h1(raw)
    f = load_funding(Path(win["perp_dir"]), "BTCUSDT")
    fund = f.groupby(f.index.floor("1h")).sum()
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    report: dict = {"plan": "docs/research/r17-short-sleeve-plan.md @ a923448", "commit": commit, "window": [start, end]}
    bh = run_r15.buy_and_hold(spot, start, end, run_r15.Costs())
    report["buy_and_hold"] = m(bh)
    for cname, mult in (("base", 1), ("costs_x2", 2)):
        r10a, _ = simulate_weights(daily, feats["r10a_w"], pd.Timestamp(start), pd.Timestamp(end), equity=10_000.0,
                                   costs=RegimeCosts(fee=0.001 * mult, slip_bps=2.0 * mult))
        g, trades, st = sleeve(h1, fund, df, start, end, FEE * mult, SLIP * mult)
        gd = g.reindex(r10a.index).ffill().fillna(1.0)
        combo = r10a * gd
        res = {"R10a": m(r10a), "R10a_plus_short": m(combo), "sleeve_alone": {"net_frac_of_equity": float(g.iloc[-1] - 1),
               "max_dd_of_G": max_drawdown(g)}, **st, "trades": int(len(trades))}
        if len(trades):
            res |= {"win_rate": float((trades["pnl_frac_of_equity"] > 0).mean()), "avg_days": float(trades["days"].mean()),
                    "exits": trades["why"].value_counts().to_dict(), "avg_notional_frac": float(trades["notional_frac"].mean()),
                    "avg_sl_dist_pct": float(trades["sl_dist_pct"].mean()),
                    "min_liq_dist_isolated_1x_pct": float(trades["liq_dist_isolated_1x_pct"].min()),
                    "min_liq_dist_cross_pct": float(trades["liq_dist_cross_account_pct"].min()),
                    "worst_trade_pct": float(trades["pnl_frac_of_equity"].min() * 100),
                    "best_trade_pct": float(trades["pnl_frac_of_equity"].max() * 100)}
        res["days_in_regime"] = int(df.loc[(df.index >= start) & (df.index < end), "regime"].sum())
        report[cname] = res
        if cname == "base":
            trades.to_csv(out / "trades.csv", index=False)
            pd.DataFrame({"R10a": r10a, "R10a_plus_short": combo, "sleeve_G": gd, "buy_and_hold": bh}).to_csv(out / "equity.csv")
    b = report["base"]
    report["verdict"] = {
        "sleeve_net_positive": bool(b["sleeve_alone"]["net_frac_of_equity"] > 0),
        "combo_beats_R10a": bool(b["R10a_plus_short"]["sharpe"] > b["R10a"]["sharpe"]
                                 and b["R10a_plus_short"]["max_dd"] <= b["R10a"]["max_dd"] + 0.02),
    }
    (out / "report.json").write_text(json.dumps(jsonable(report), indent=2))
    print(json.dumps(jsonable({k: report[k] for k in ("window", "buy_and_hold", "base", "verdict")}), indent=1))
    print("costs_x2:", json.dumps(jsonable({k: report["costs_x2"][k] for k in ("R10a", "R10a_plus_short", "sleeve_alone")})))


if __name__ == "__main__":
    main()
