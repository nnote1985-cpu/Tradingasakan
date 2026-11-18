import numpy as np
import pandas as pd

from trading.features.orderbook import (
    find_walls,
    imbalance,
    near_depth_ratio_from_bookdepth,
    obi_band,
    obi_from_bookdepth,
    obi_levels,
    snapshot_features,
    trade_flow_bars,
)


def test_imbalance_sign_and_empty():
    assert imbalance(3.0, 1.0) == 0.5
    assert imbalance(1.0, 3.0) == -0.5
    assert np.isnan(imbalance(0.0, 0.0))


def test_obi_levels_and_band():
    bids = [[99.9, 1.0], [99.8, 3.0], [90.0, 100.0]]
    asks = [[100.1, 1.0], [100.2, 1.0]]
    assert obi_levels(bids, asks, 2) == (4 - 2) / 6
    # 1% band around 100 excludes the 90.0 bid
    b = 99.9 + 99.8 * 3
    a = 100.1 + 100.2
    assert abs(obi_band(bids, asks, 100.0, 1.0) - (b - a) / (b + a)) < 1e-12


def test_trade_flow_bars():
    t = pd.to_datetime(["2026-11-01 00:00:10", "2026-11-01 00:00:20", "2026-11-01 00:01:05"], utc=True)
    tr = pd.DataFrame({"qty": [2.0, 1.0, 4.0], "is_buyer_maker": [False, True, True]}, index=t)
    f = trade_flow_bars(tr, "1min")
    assert f["buy"].tolist() == [2.0, 0.0]
    assert f["sell"].tolist() == [1.0, 4.0]
    assert f["delta"].tolist() == [1.0, -4.0]
    assert f["cvd"].tolist() == [1.0, -3.0]
    assert abs(f["delta_ratio"].iloc[0] - 1 / 3) < 1e-12


def test_find_walls():
    bids = [[99.99, 1.0], [99.98, 1.0], [99.97, 10.0], [99.96, 1.0]]
    asks = [[100.01, 1.0], [100.02, 1.0], [100.03, 1.0]]
    w = find_walls(bids, asks, 100.0, k=5.0, max_bps=50)
    assert len(w) == 1 and w[0].side == "bid" and w[0].price == 99.97
    assert abs(w[0].dist_bps - 3.0) < 1e-6


def _depth_frame():
    rows = []
    for ts in ("2026-11-01 00:00:01", "2026-11-01 00:00:30"):
        for p, n in ((-5, 500), (-1, 200), (-0.2, 60), (0.2, 20), (1, 100), (5, 500)):
            rows.append({"timestamp": pd.Timestamp(ts, tz="UTC"), "percentage": float(p), "depth": 0.0, "notional": float(n)})
    return pd.DataFrame(rows)


def test_bookdepth_adapters():
    d = _depth_frame()
    obi = obi_from_bookdepth(d, 0.2)
    assert obi.tolist() == [0.5, 0.5]
    near = near_depth_ratio_from_bookdepth(d)
    # bid: 200 within 1%, (500-200)/4 = 75 per 1% outside -> 2.667; ask: 100 vs 100 -> 1.0
    assert abs(near["bid_near_ratio"].iloc[0] - 200 / 75) < 1e-9
    assert near["ask_near_ratio"].iloc[0] == 1.0


def test_snapshot_features_on_collector_record():
    rec = {"t": 1793491200000, "mid": 100.0, "b": [[99.99, 2.0], [99.98, 2.0]], "a": [[100.01, 1.0], [100.02, 1.0]]}
    f = snapshot_features(rec, top_n=2, band_pct=0.1)
    assert f["obi5"] == (4 - 2) / 6
    assert f["bid_wall_bps"] == float("inf")
