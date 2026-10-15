import numpy as np
import pandas as pd
from test_backtest import FLAT, T0, make
from trading.simulator.bar_backtest import ExitParams
from trading.strategies.trend_donchian_4h_v1_1 import DonchianParams, build_features

NO_TP = ExitParams(take_profit_r=None, time_exit=pd.Timedelta(days=60))


def _h4(close_times, long_exit, short_exit=None, close=60000.0, ema_slow=50000.0):
    n = len(close_times)
    return pd.DataFrame({
        "close_time": close_times, "close": [close] * n, "ema_slow": [ema_slow] * n,
        "long_exit": long_exit, "short_exit": short_exit or [False] * n,
    })


def test_no_take_profit_lets_winner_run():
    bars = [FLAT, FLAT, (60000, 70000, 59950, 69000), (69000, 69500, 68500, 69000)]
    res = make(bars, {0: "LONG"}, exits=NO_TP).run()
    tr = res.trades.iloc[0]
    assert tr["exit_reason"] == "END_OF_DATA"
    assert tr["exit_price"] == 69000


def test_channel_exit_at_next_open_after_flagged_4h_close():
    # 4h bars closing at 04:00 (no exit) and 08:00 (exit flag).
    h4 = _h4([T0 + pd.Timedelta(hours=4), T0 + pd.Timedelta(hours=8)], [False, True])
    tr = make([FLAT] * 12, {0: "LONG"}, h4=h4, exits=NO_TP).run().trades.iloc[0]
    assert tr["exit_reason"] == "CHANNEL_EXIT"
    assert tr["exit_time"] == T0 + pd.Timedelta(hours=8)


def test_channel_flag_known_at_entry_is_not_used():
    # Flag on the 4h bar closing at 01:00, already closed when the position opens.
    h4 = _h4([T0 + pd.Timedelta(hours=1)], [True])
    res = make([FLAT] * 6, {0: "LONG"}, h4=h4, exits=NO_TP).run()
    assert res.trades.iloc[0]["exit_reason"] == "END_OF_DATA"


def test_short_uses_short_exit_flag():
    h4 = _h4([T0 + pd.Timedelta(hours=4)], [True], [False], close=40000.0, ema_slow=50000.0)
    res = make([FLAT] * 8, {0: "SHORT"}, h4=h4, exits=NO_TP).run()
    assert res.trades.iloc[0]["exit_reason"] == "END_OF_DATA"


def _h1_series(closes):
    idx = pd.date_range("2020-01-01", periods=len(closes), freq="1h", tz="UTC")
    c = np.asarray(closes, float)
    return pd.DataFrame({"open": c, "high": c + 0.1, "low": c - 0.1, "close": c, "volume": 1.0}, index=idx)


def test_signal_long_on_4h_breakout_in_uptrend_only_after_warmup():
    p = DonchianParams(warmup_4h_bars=250)
    n4 = 300
    closes = np.repeat(np.linspace(100, 200, n4), 4)   # steady uptrend, new highs every bar
    sig, h4 = build_features(_h1_series(closes), p)
    assert (sig["signal"].iloc[: p.warmup_4h_bars - 1] == "NO_TRADE").all()
    assert (sig["signal"].iloc[p.warmup_4h_bars:] == "LONG").all()
    assert (sig["close_time"] == sig.index + pd.Timedelta(hours=4)).all()
    assert not h4["long_exit"].any()


def test_signal_none_in_range_and_exit_flag_on_breakdown():
    p = DonchianParams(warmup_4h_bars=250)
    up = np.linspace(100, 200, 280)
    down = np.linspace(199, 150, 20)
    closes = np.repeat(np.concatenate([up, down]), 4)
    sig, h4 = build_features(_h1_series(closes), p)
    assert (sig["signal"].iloc[280:] != "LONG").all()
    assert h4["long_exit"].iloc[281:].any()
