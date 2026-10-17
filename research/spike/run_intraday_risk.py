"""Intraday risk of R7 / R11, pre-declared in docs/research/intraday-risk-plan.md (commit 60c2d18).

    PYTHONPATH=research/spike uv run python research/spike/run_intraday_risk.py

Development data only (data/binance_um, 2022-01-01 -> 2025-12-31). Q1 marks the unchanged
run_r7.simulate positions every hour; Q2 replays them on 1h bars with an exchange stop at k% from
entry, or with an hourly-checked 25% HALT.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import run_r7
import run_r11
from common import jsonable

from trading.evaluation.metrics import daily_log_returns, max_drawdown, sharpe
from trading.market_data.binance_public import load, load_funding
from trading.strategies.trend_breakout_v1 import regularize_h1

START, END = run_r11.START, run_r11.END
STOPS = (0.05, 0.10, 0.15, 0.20, 0.25)
HALT_DD, ALERT_DD = 0.25, 0.15
BOOKS = {"R7": run_r7.side_state, "R11": run_r11.band_side}


def hourly() -> tuple[pd.DataFrame, pd.Series]:
    h1 = regularize_h1(load(run_r11.DATA, "BTCUSDT", "1h"))
    f = load_funding(run_r11.DATA, "BTCUSDT")
    f.index = f.index.floor("1h")
    return h1, f.groupby(level=0).sum()


def simulate_h(daily, h1, funding, side_fn, start, end, *, stop: float | None = None, halt: str | None = None):
    """run_r7.simulate on 1h bars. stop: reduce-only stop at k from entry; halt: 'hourly' or 'daily'
    25% equity drawdown HALT (absorbing). Returns hourly marks (close, worst intrabar), daily close
    equity and stats."""
    fee, slip = run_r7.FEE, run_r7.SLIP
    days = daily.loc[(daily.index >= pd.Timestamp(start, tz="UTC")) & (daily.index < pd.Timestamp(end, tz="UTC"))]
    target = side_fn(daily).shift(1).fillna(0.0).reindex(days.index)
    cash, side, qty, entry, entry_eq = run_r7.EQUITY, 0, 0.0, np.nan, run_r7.EQUITY
    stopped_side, halted, halt_pending = 0, False, False
    peak_h = peak_d = run_r7.EQUITY
    last = np.nan
    st = {"stops": 0, "halt_at": None, "liquidated": None, "fees": 0.0, "funding_paid": 0.0, "flips": 0}
    h_close, h_worst, d_close = {}, {}, {}
    pos = {t: i for i, t in enumerate(h1.index)}
    arr = h1[["open", "high", "low", "close"]].to_numpy()
    valid = h1["valid"].to_numpy()
    fund = funding.to_dict()

    def equity(px):
        return cash + (side * qty * (px - entry) if side else 0.0)

    def close_at(px_raw, why):
        nonlocal cash, side, qty
        px = px_raw * (1 - side * slip)
        cash += side * qty * (px - entry)
        f = qty * px * fee
        cash -= f
        st["fees"] += f
        side, qty = 0, 0.0

    for d, o, tg in zip(days.index, days["open"].to_numpy(), target.to_numpy(), strict=True):
        want = 0 if halted else int(tg)
        if halt_pending and np.isfinite(o):          # daily-checked HALT fills at the next day's open
            if side:
                close_at(o, "halt")
            halted, halt_pending, want = True, False, 0
        if want != stopped_side:
            stopped_side = 0
        if np.isfinite(o) and want != side and not (want and want == stopped_side):
            if side:
                close_at(o, "flip")
            if want:
                px = o * (1 + want * slip)
                qty = cash / px
                f = qty * px * fee
                cash -= f
                st["fees"] += f
                side, entry, entry_eq = want, px, cash
                st["flips"] += 1
        for h in pd.date_range(d, periods=24, freq="1h"):
            i = pos.get(h)
            if i is None or not valid[i]:
                continue
            bo, bh, bl, bc = arr[i]
            if halt == "hourly" and halt_pending:     # hourly-checked HALT fills at the next 1h open
                if side:
                    close_at(bo, "halt")
                halted, halt_pending = True, False
            if side and h in fund:
                pay = side * qty * bo * float(fund[h])
                cash -= pay
                st["funding_paid"] += pay
            if side and stop is not None:
                sp = entry * (1 - side * stop)
                if (side > 0 and bl <= sp) or (side < 0 and bh >= sp):
                    fill = min(bo, sp) if side > 0 else max(bo, sp)
                    s0 = side
                    close_at(fill, "stop")
                    stopped_side = s0
                    st["stops"] += 1
            if side:
                worst = bh if side < 0 else bl
                if equity(worst) <= run_r7.LIQ_FRACTION * entry_eq:
                    st["liquidated"] = str(h)
                    cash, side, qty = equity(worst), 0, 0.0
                h_worst[h] = equity(worst)
            else:
                h_worst[h] = cash
            last = bc
            e = equity(bc)
            h_close[h] = e
            peak_h = max(peak_h, e)
            if halt == "hourly" and not halted and not halt_pending and e <= (1 - HALT_DD) * peak_h:
                halt_pending, st["halt_at"] = True, str(h)
        e = equity(last) if np.isfinite(last) else cash
        d_close[d] = e
        peak_d = max(peak_d, e)
        if halt == "daily" and not halted and not halt_pending and e <= (1 - HALT_DD) * peak_d:
            halt_pending, st["halt_at"] = True, str(d.date())
    start_pt = pd.Series([run_r7.EQUITY], index=[days.index[0] - pd.Timedelta(days=1)])
    eq_d = pd.concat([start_pt, pd.Series(d_close)])
    return pd.Series(h_close), pd.Series(h_worst), eq_d, st


def dd_series(eq: pd.Series) -> pd.Series:
    return 1 - eq / eq.cummax()


def first_trip(eq: pd.Series, level: float):
    hit = dd_series(eq) >= level
    return str(hit.idxmax()) if hit.any() else None


def q1(name, daily, h1, funding):
    hc, hw, eq_d, st = simulate_h(daily, h1, funding, BOOKS[name], START, END)
    run_r7.side_state = BOOKS[name]
    ref, _, _ = run_r7.simulate(daily, funding.groupby(funding.index.floor("1D")).sum(), START, END)
    run_r7.side_state = BOOKS["R7"]
    peak = pd.concat([pd.Series([run_r7.EQUITY]), hc]).cummax().iloc[1:]
    worst_dd = float((1 - hw / peak.reindex(hw.index).ffill()).max())
    return {
        "net_hourly_sim": float(eq_d.iloc[-1] / run_r7.EQUITY - 1),
        "net_run_r7_simulate": float(ref.iloc[-1] / run_r7.EQUITY - 1),
        "max_dd_daily_close": max_drawdown(eq_d), "max_dd_hourly_close": max_drawdown(hc),
        "max_dd_worst_intrabar": worst_dd,
        "worst_intraday_drop_from_prior_close": float((1 - hw.groupby(hw.index.floor("1D")).min()
                                                      / eq_d.shift(1).reindex(hw.index.floor("1D").unique())).max()),
        "least_margin_buffer": float(hw.min() / run_r7.EQUITY),
        "alert15_first_daily": first_trip(eq_d, ALERT_DD), "alert15_first_hourly": first_trip(hc, ALERT_DD),
        "halt25_first_daily": first_trip(eq_d, HALT_DD), "halt25_first_hourly": first_trip(hc, HALT_DD),
        "liquidated": st["liquidated"],
    }


def summary(eq_d, hc, st):
    return {"net": float(eq_d.iloc[-1] / run_r7.EQUITY - 1), "sharpe": sharpe(daily_log_returns(eq_d)),
            "max_dd_hourly": max_drawdown(hc), "stops": st["stops"], "flips": st["flips"],
            "halt_at": st["halt_at"], "fees": st["fees"], "funding_paid": st["funding_paid"]}


def main() -> None:
    np.seterr(all="ignore")
    daily, _ = run_r11.market()
    h1, funding = hourly()
    out = Path("reports/intraday-risk")
    out.mkdir(parents=True, exist_ok=True)
    report: dict = {"plan": "docs/research/intraday-risk-plan.md @ 60c2d18", "window": [START, END],
                    "q1": {n: q1(n, daily, h1, funding) for n in BOOKS}, "q2": {}}
    base_fee, base_slip = run_r7.FEE, run_r7.SLIP
    for costs_name, mult in (("base_costs", 1), ("costs_x2", 2)):
        run_r7.FEE, run_r7.SLIP = base_fee * mult, base_slip * mult
        report["q2"][costs_name] = {}
        for n, fn in BOOKS.items():
            runs = {}
            for label, kw in ([("no_stop", {})] + [(f"stop_{int(k * 100)}", {"stop": k}) for k in STOPS]
                              + [("halt25_daily", {"halt": "daily"}), ("halt25_hourly", {"halt": "hourly"})]):
                hc, _, eq_d, st = simulate_h(daily, h1, funding, fn, START, END, **kw)
                runs[label] = summary(eq_d, hc, st)
            report["q2"][costs_name][n] = runs
    run_r7.FEE, run_r7.SLIP = base_fee, base_slip
    ok = all(abs(v["net_hourly_sim"] - v["net_run_r7_simulate"]) <= 0.005 for v in report["q1"].values())
    report["reproduces_run_r7"] = ok
    harmless = []
    for k in STOPS:
        lab = f"stop_{int(k * 100)}"
        if all((b := report["q2"]["base_costs"][n])[lab]["sharpe"] >= b["no_stop"]["sharpe"] - 0.05
               and b[lab]["net"] >= b["no_stop"]["net"] - 0.02 for n in BOOKS):
            harmless.append(k)
    report["tightest_harmless_stop"] = min(harmless) if (ok and harmless) else None
    if not ok:
        report["q2"] = "not reported: the hourly simulator does not reproduce run_r7.simulate"
    (out / "report.json").write_text(json.dumps(jsonable(report), indent=2))
    print(json.dumps(jsonable(report), indent=2))


if __name__ == "__main__":
    main()
