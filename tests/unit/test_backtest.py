from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
from trading.risk.sizing import SizingAllowances
from trading.simulator.bar_backtest import BacktestConfig, BarBacktester, CostModel

T0 = pd.Timestamp("2024-03-01 00:00", tz="UTC")
NO_SLIP = CostModel(taker_fee=0.0005, entry_slip_bps=0, exit_slip_bps=0, stop_slip_bps=0)
NO_FUND_RESERVE = SizingAllowances(funding_reserve_fraction=Decimal("0"))


def make(bars, signals, *, h4=None, funding=None, **cfg):
    """bars: list of (o, h, l, c) hourly from T0; signals: {bar_index: side}."""
    idx = pd.date_range(T0, periods=len(bars), freq="1h", tz="UTC")
    ex = pd.DataFrame(bars, columns=["open", "high", "low", "close"], index=idx)
    h1 = pd.DataFrame({"signal": "NO_TRADE", "close_time": idx + pd.Timedelta(hours=1),
                       "atr": 300.0}, index=idx)
    for k, side in signals.items():
        h1.iloc[k, h1.columns.get_loc("signal")] = side
    if h4 is None:
        h4 = pd.DataFrame(columns=["close_time", "close", "ema_slow"])
    config = BacktestConfig(costs=cfg.pop("costs", NO_SLIP),
                            allowances=cfg.pop("allowances", NO_FUND_RESERVE), **cfg)
    return BarBacktester(ex, h1, h4, funding, config)


FLAT = (60000, 60100, 59900, 60000)


def test_entry_uses_first_open_after_signal_close():
    bt = make([(59000, 59000, 59000, 60000), FLAT, FLAT], {0: "LONG"})
    res = bt.run()
    tr = res.trades.iloc[0]
    assert tr["entry_time"] == T0 + pd.Timedelta(hours=1)
    assert tr["entry_price"] == 60000          # bar-1 open, not bar-0 close
    assert tr["stop"] == 59400 and tr["take_profit"] == 61200
    assert tr["qty"] == pytest.approx(0.069)


@pytest.mark.parametrize("ambiguity,reason,price", [
    ("conservative", "STOP", 59400), ("optimistic", "TAKE_PROFIT", 61200),
])
def test_T13_same_bar_sl_and_tp(ambiguity, reason, price):
    bt = make([FLAT, FLAT, (60000, 61300, 59300, 60500)], {0: "LONG"}, ambiguity=ambiguity)
    tr = bt.run().trades.iloc[0]
    assert tr["exit_reason"] == reason and tr["exit_price"] == price
    assert bool(tr["ambiguous_bar"])


def test_T14_gap_through_stop_fills_at_open():
    bt = make([FLAT, FLAT, (58900, 59000, 58800, 58950)], {0: "LONG"})
    tr = bt.run().trades.iloc[0]
    assert tr["exit_reason"] == "STOP_GAP" and tr["exit_price"] == 58900


def test_T15_ledger_matches_worked_example():
    # Funding at 02:00 paid on 0.069 BTC at open 60000 → exactly 0.50 USDT.
    rate = 0.5 / (0.069 * 60000)
    funding = pd.Series([rate], index=[T0 + pd.Timedelta(hours=2)])
    bt = make([FLAT, FLAT, FLAT, (60000, 61250, 59950, 61000)], {0: "LONG"}, funding=funding)
    res = bt.run()
    tr = res.trades.iloc[0]
    assert tr["gross_pnl"] == pytest.approx(82.8)
    assert tr["fees"] == pytest.approx(2.07 + 2.1114)
    assert tr["funding"] == pytest.approx(-0.5)
    assert tr["net_pnl"] == pytest.approx(78.1186)
    assert res.stats["final_equity"] == pytest.approx(10_000 + 78.1186)


def test_time_exit_after_24h():
    bars = [FLAT] * 30
    tr = make(bars, {0: "LONG"}).run().trades.iloc[0]
    assert tr["exit_reason"] == "TIME_EXIT"
    assert tr["exit_time"] == T0 + pd.Timedelta(hours=25)


def test_trend_exit_when_latest_closed_4h_crosses_ema200():
    h4 = pd.DataFrame({"close_time": [T0 + pd.Timedelta(hours=4)], "close": [59000.0],
                       "ema_slow": [59500.0]})
    tr = make([FLAT] * 8, {0: "LONG"}, h4=h4).run().trades.iloc[0]
    assert tr["exit_reason"] == "TREND_EXIT"
    assert tr["exit_time"] == T0 + pd.Timedelta(hours=4)


def test_cooldown_blocks_candidate_two_hours_after_exit():
    bars = [FLAT, FLAT, (60000, 61300, 59950, 61000), FLAT, FLAT, FLAT]
    res = make(bars, {0: "LONG", 2: "LONG", 4: "LONG"}).run()
    statuses = res.candidates.set_index("cand_id")["status"].tolist()
    # bar-2 TP exit at 03:00; candidate available 03:00 → cooldown; 05:00 → opened.
    assert statuses == ["OPENED", "NOT_EVALUATED_COOLDOWN", "OPENED"]


def test_filter_veto_is_logged_and_skips_entry():
    res = make([FLAT, FLAT, FLAT], {0: "LONG"}).run(filter_fn=lambda c: False)
    assert res.trades.empty
    assert res.candidates["status"].tolist() == ["FILTER_VETO"]


def test_drawdown_breach_absorbing_halts_new_entries():
    crash = (60000, 60000, 50000, 50000)
    bars = [FLAT, FLAT, crash] + [FLAT] * 6
    res = make(bars, {0: "LONG", 5: "LONG"}).run()
    # Stop at 59400 limits the loss; no breach expected from one trade.
    assert res.trades.iloc[0]["exit_reason"] == "STOP"
    assert res.trades["net_pnl"].iloc[0] < 0
    assert len(res.trades) == 2


def test_missing_bar_while_in_position_is_counted():
    bars = [FLAT, FLAT, (np.nan,) * 4, FLAT]
    res = make(bars, {0: "LONG"}).run()
    assert res.stats["incomplete_bars_in_position"] == 1
