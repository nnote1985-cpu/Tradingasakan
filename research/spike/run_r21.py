"""R21: BTC rules on ETH, period ETH-1 (2018-2021), pre-declared in docs/research/r21-eth1-cross-asset-plan.md.

    PYTHONPATH=research/spike uv run python research/spike/run_r21.py
"""

from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import run_r15
import run_r17
from common import jsonable
from run_r10 import BAND, latch

from trading.market_data.binance_public import _download_zip, _months, load, load_funding, parse_klines_csv
from trading.strategies.funding_overlay_v3 import simulate_weights
from trading.strategies.trend_breakout_v1 import regularize_h1
from trading.strategies.trend_regime_v2 import RegimeCosts

SYM = "ETHUSDT"
OUT = Path("/mnt/project-files/r21-results")
WIN_F = {"range": ("2020-04-15", "2022-01-01"), "data": (date(2020, 1, 1), date(2021, 12, 31)),
         "perp_dir": "data/eth_um_1", "spot_dir": "data/eth_spot_1", "years": []}
R17_START = "2020-08-01"
WIN_S = ("2018-04-11", "2022-01-01")


def stats(e: pd.Series) -> dict:
    e = e.dropna()
    r = np.log(e / e.shift()).dropna()
    yrs = (e.index[-1] - e.index[0]).days / 365
    qe = e.groupby(e.index.to_period("Q")).last()
    qs = np.r_[e.iloc[0], qe.to_numpy()[:-1]]
    ye = e.groupby(e.index.year).last()
    ys = np.r_[e.iloc[0], ye.to_numpy()[:-1]]
    return {"net": float(e.iloc[-1] / e.iloc[0] - 1), "annual": float((e.iloc[-1] / e.iloc[0]) ** (1 / yrs) - 1),
            "max_dd": float(((e.cummax() - e) / e.cummax()).max()), "sharpe": float(r.mean() / r.std() * np.sqrt(365)),
            "quarters_non_negative": f"{int((qe.to_numpy() / qs >= 1).sum())}/{len(qe)}",
            "years_non_negative": f"{int((ye.to_numpy() / ys >= 1).sum())}/{len(ye)}",
            "years": {int(y): float(v) for y, v in zip(ye.index, ye.to_numpy() / ys - 1, strict=True)}}


def spot_daily(a: date, b: date) -> pd.DataFrame:
    d = Path("data/eth_spot_1s")
    (d / "raw").mkdir(parents=True, exist_ok=True)
    frames = []
    for y, m in _months(a, b):
        inner, info = _download_zip(f"{run_r15.SPOT_BASE}/monthly/klines/{SYM}/1d/{SYM}-1d-{y}-{m:02d}.zip", d / "raw")
        if info["status"] != "VERIFIED":
            raise RuntimeError(info)
        frames.append(parse_klines_csv(inner))
    s = pd.concat(frames).sort_index()
    s.index = s.index.tz_localize(None).floor("1D")
    if s.index.max() >= pd.Timestamp("2022-01-01"):
        raise RuntimeError("ETH-2 guard")
    return s[~s.index.duplicated()]


def main() -> None:
    np.seterr(all="ignore")
    run_r15.SYMBOL = SYM
    OUT.mkdir(parents=True, exist_ok=True)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    rep: dict = {"plan": "docs/research/r21-eth1-cross-asset-plan.md", "commit": commit}
    start, end = WIN_F["range"]
    daily, spot, fd, info = run_r15.market(WIN_F)
    tg, feats = run_r15.targets(daily, fd)
    rep["data_futures"] = info
    curves: dict[str, pd.Series] = {}
    r10a, _ = simulate_weights(daily, feats["r10a_w"], pd.Timestamp(start), pd.Timestamp(end), equity=10_000.0, costs=RegimeCosts())
    curves["1 spot R10a"] = r10a
    ws, wp = tg["F1_gated_ls_1x"]
    curves["2 futures L/S 1x"] = run_r15.simulate(daily, spot, fd, ws, wp, start, end, run_r15.Costs())[0]
    w = feats["r10a_w"]
    u = w.where(w.notna())
    curves["3a mixed hedge (R10a+carry)"] = run_r15.simulate(daily, spot, fd, u + 0.5 * (1 - u), -0.5 * (1 - u), start, end,
                                                             run_r15.Costs())[0]
    one = pd.Series(1.0, index=w.index)
    curves["base carry K0"] = run_r15.simulate(daily, spot, fd, one * 0.5, one * -0.5, start, end, run_r15.Costs())[0]
    curves["B&H ETH"] = run_r15.buy_and_hold(spot, start, end, run_r15.Costs())
    # 3b: R17 sleeve beside R10a, from 2020-08-01 (SMA200 warm-up)
    df = run_r17.daily_features(daily, feats)
    h1 = regularize_h1(load(Path(WIN_F["perp_dir"]), SYM, "1h"))
    f = load_funding(Path(WIN_F["perp_dir"]), SYM)
    fund = f.groupby(f.index.floor("1h")).sum()
    g, trades, st = run_r17.sleeve(h1, fund, df, R17_START, end)
    r10_b, _ = simulate_weights(daily, feats["r10a_w"], pd.Timestamp(R17_START), pd.Timestamp(end), equity=10_000.0, costs=RegimeCosts())
    curves["3b mixed net short (R17)*"] = r10_b * g.reindex(r10_b.index).ffill().fillna(1.0)
    curves["  R10a same window*"] = r10_b
    rep["r17"] = {"trades": int(len(trades)), **st,
                  "sleeve_net_frac": float(g.iloc[-1] - 1) if len(g) else 0.0,
                  "exits": trades["why"].value_counts().to_dict() if len(trades) else {}}
    rep["eth_1f"] = {k: stats(v) for k, v in curves.items()}
    pd.DataFrame(curves).to_csv(OUT / "equity_eth1f.csv")
    # ETH-1S: spot only, full period
    sd = spot_daily(date(2018, 1, 1), date(2021, 12, 31))
    c = sd["close"]
    sma = c.rolling(100, min_periods=100).mean()
    band = latch(c, sma, lambda i: c.iloc[i] > sma.iloc[i] * (1 + BAND), lambda i: c.iloc[i] < sma.iloc[i] * (1 - BAND))
    s_r10a, _ = simulate_weights(sd, band, pd.Timestamp(WIN_S[0]), pd.Timestamp(WIN_S[1]), equity=10_000.0, costs=RegimeCosts())
    days = sd.loc[(sd.index >= WIN_S[0]) & (sd.index < WIN_S[1])]
    qty = 10_000 * (1 - 0.001) / (days["open"].iloc[0] * 1.0002)
    s_bh = pd.concat([pd.Series([10_000.0], index=[days.index[0] - pd.Timedelta(days=1)]), qty * days["close"]])
    rep["eth_1s"] = {"R10a spot (no funding rule)": stats(s_r10a), "B&H ETH": stats(s_bh)}
    pd.DataFrame({"R10a": s_r10a, "B&H": s_bh}).to_csv(OUT / "equity_eth1s.csv")
    (OUT / "report.json").write_text(json.dumps(jsonable(rep), indent=2))
    for part in ("eth_1f", "eth_1s"):
        print(part)
        for k, v in rep[part].items():
            print(f"  {k:30s} {v['annual']:+7.0%}/yr dd {v['max_dd']:.0%} sh {v['sharpe']:.2f} Q+ {v['quarters_non_negative']} "
                  f"Y+ {v['years_non_negative']} years {({y: round(x * 100) for y, x in v['years'].items()})}")
    print("r17", rep["r17"])


if __name__ == "__main__":
    main()
