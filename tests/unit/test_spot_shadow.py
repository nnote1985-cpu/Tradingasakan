"""Spot accounting in the Shadow engine (docs/plan-spot-and-options.md, spot track steps 1-2)."""

import json
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from trading.risk.sizing import InstrumentRules, RiskProfile
from trading.shadow.portfolio import ShadowConfig, ShadowPortfolio
from trading.shadow.spot_ledger import InsufficientBalance, SpotLedger
from trading.simulator.bar_backtest import BacktestConfig, BarBacktester, CostModel

T0 = pd.Timestamp("2024-03-01 00:00", tz="UTC")
SPOT_COSTS = CostModel(taker_fee=0.001, entry_slip_bps=2, exit_slip_bps=2, stop_slip_bps=5)
NO_LEV = RiskProfile(max_gross_notional_to_equity=Decimal("1"), requested_exchange_leverage=Decimal("1"),
                     max_margin_utilization_fraction=Decimal("1"))
ZERO_DELAY = dict(decision_delay=pd.Timedelta(0), network_delay=pd.Timedelta(0))


def test_spot_round_trip_books_cost_proceeds_and_fees():
    led = SpotLedger()
    led.open("open", T0, 10000)
    led.fill("b", T0, "BUY", "0.1", "60000", "6")
    assert led.bal("cash") == Decimal("3994") and led.position.qty == Decimal("0.1")
    assert led.trading_equity(61000) == Decimal("10094")
    led.fill("s", T0, "SELL", "0.1", "61200", "6.12")
    assert led.bal("cash") == Decimal("10107.88")                  # 120 gross − 6 − 6.12
    assert led.bal("realized_pnl") == Decimal("-120") and led.position.qty == 0
    assert led.check() == []


def test_spot_refuses_overspending_and_short_sales():
    led = SpotLedger()
    led.open("open", T0, 1000)
    with pytest.raises(InsufficientBalance):
        led.fill("b", T0, "BUY", "0.1", "60000", "0")
    led.fill("b", T0, "BUY", "0.01", "60000", "0.6")
    with pytest.raises(InsufficientBalance):
        led.fill("s", T0, "SELL", "0.02", "60000", "0")
    assert led.check() == []


def test_spot_duplicates_and_funding_change_nothing():
    led = SpotLedger()
    led.open("open", T0, 10000)
    led.fill("b", T0, "BUY", "0.1", "60000", "6")
    snap = (led.bal("cash"), led.position.qty, len(led.entries))
    assert not led.fill("b", T0, "BUY", "0.1", "60000", "6")
    assert not led.funding("f", T0, "0.001", "60000")
    assert (led.bal("cash"), led.position.qty, len(led.entries)) == snap


def test_partial_sell_uses_average_cost():
    led = SpotLedger()
    led.open("open", T0, 20000)
    led.fill("a", T0, "BUY", "0.1", "60000", "0")
    led.fill("b", T0, "BUY", "0.1", "62000", "0")
    led.fill("c", T0, "SELL", "0.05", "63000", "0")
    assert led.bal("realized_pnl") == Decimal("-100") and led.position.avg_entry == Decimal("61000")
    again = SpotLedger.from_dict(json.loads(json.dumps(led.to_dict())))
    assert again.balances == led.balances and again.check() == []


def frames(bars, signals):
    idx = pd.date_range(T0, periods=len(bars), freq="1h", tz="UTC")
    ex = pd.DataFrame(bars, columns=["open", "high", "low", "close"], index=idx, dtype=float)
    h1 = pd.DataFrame({"signal": "NO_TRADE", "close_time": idx + pd.Timedelta(hours=1), "atr": 300.0}, index=idx)
    for k, side in signals.items():
        h1.iloc[k, h1.columns.get_loc("signal")] = side
    return ex, h1, pd.DataFrame(columns=["close_time", "close", "ema_slow"])


def spot_book(**kw):
    return ShadowPortfolio(ShadowConfig(instrument_type="SPOT", costs=SPOT_COSTS, profile=kw.pop("profile", NO_LEV),
                                        rules=InstrumentRules(tick=Decimal("0.01"), qty_step=Decimal("0.00001"),
                                                              min_notional=Decimal("10")), **ZERO_DELAY, **kw))


FLAT = (60000, 60100, 59900, 60000)


def test_spot_book_rejects_shorts_and_charges_no_funding():
    ex, h1, h4 = frames([FLAT] * 12, {0: "SHORT", 2: "LONG"})
    fund = pd.Series(0.01, index=pd.date_range(T0, periods=2, freq="8h", tz="UTC"))
    book = spot_book()
    book.run(ex, h1, h4, fund)
    assert [c["status"] for c in book.candidates] == ["REJECT_NO_SHORT_ON_SPOT", "OPENED"]
    assert book.ledger.bal("funding") == 0
    tr, _ = book.equity()
    assert tr == book.ledger.bal("cash") + book.ledger.position.qty * Decimal("60000")


def test_full_equity_spot_buy_is_capped_by_cash_including_fee():
    big = RiskProfile(risk_per_trade_fraction=Decimal("1"), absolute_trade_risk_cap_usdt=Decimal("1e12"),
                      daily_loss_trigger_fraction=Decimal("1"), max_gross_notional_to_equity=Decimal("1"),
                      requested_exchange_leverage=Decimal("1"), max_margin_utilization_fraction=Decimal("1"))
    ex, h1, h4 = frames([FLAT] * 6, {0: "LONG"})
    book = spot_book(profile=big)
    book.run(ex, h1, h4)
    assert book.pos is not None and book.ledger.bal("cash") >= 0
    assert book.ledger.bal("cash") < Decimal("20")                 # nearly all cash is in BTC
    assert book.ledger.check() == []


@pytest.mark.parametrize("seed", [4, 5])
def test_spot_book_matches_bar_backtest_on_long_only_signals(seed):
    rng = np.random.default_rng(seed)
    n = 24 * 30
    c = rng.normal(0, 250, n).cumsum() + 60000
    o = np.r_[60000, c[:-1]]
    h = np.maximum(o, c) + rng.uniform(0, 300, n)
    lo = np.minimum(o, c) - rng.uniform(0, 300, n)
    sig = {int(k): "LONG" for k in rng.choice(n - 2, n // 12, replace=False)}
    ex, h1, h4 = frames(list(zip(o, h, lo, c)), sig)
    ref = BarBacktester(ex, h1, h4, None, BacktestConfig(costs=SPOT_COSTS, profile=NO_LEV, rules=InstrumentRules(
        tick=Decimal("0.01"), qty_step=Decimal("0.00001"), min_notional=Decimal("10")))).run()
    book = spot_book()
    book.run(ex, h1, h4)
    ours = pd.DataFrame(book.trades)
    want = ref.trades[ref.trades["exit_reason"] != "END_OF_DATA"].reset_index(drop=True)
    assert len(ours) == len(want) > 5
    assert list(ours["exit_reason"]) == list(want["exit_reason"])
    assert np.allclose(ours["net_pnl"].astype(float), want["net_pnl"], atol=1e-6)
    assert book.ledger.check() == []
