"""pathA-r10a-hedge ("3a") forward book (docs/r10a-hedge-book-plan.md)."""

import json
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from trading.risk.sizing import RiskProfile
from trading.shadow.band_books import BandSpotBook
from trading.shadow.hedge_book import HedgeBook
from trading.shadow.rebalance_book import PathABook

LOOSE = RiskProfile(daily_loss_trigger_fraction=Decimal("0.99"), daily_loss_action="ALERT",
                    portfolio_drawdown_halt_fraction=None, max_gross_notional_to_equity=Decimal("1"),
                    requested_exchange_leverage=Decimal("1"), max_margin_utilization_fraction=Decimal("1"))


class NoStop(HedgeBook):
    stop_atr = None


def _market(prices, basis=0.0005, start="2026-01-01", spikes=None):
    """Spot and perp daily bars plus 1h bars whose opens all equal the day's open (so the book, which
    fills at the first 1h bar after the decision, and the backtest, which fills at the daily open,
    see one price). ``spikes`` maps day index -> 1h high on that day's 05:00 perp bar."""
    idx = pd.date_range(start, periods=len(prices), freq="D", tz="UTC")
    opens = np.r_[prices[0], prices[:-1]]
    spot = pd.DataFrame({"open": opens, "close": prices, "high": np.maximum(opens, prices) * 1.001,
                         "low": np.minimum(opens, prices) * 0.999}, index=idx)
    perp = spot * (1 + basis)
    hours = pd.date_range(idx[0], idx[-1] + pd.Timedelta(hours=23), freq="h", tz="UTC")
    day_of = hours.floor("D")
    sh = pd.DataFrame({"open": spot["open"].reindex(day_of).to_numpy()}, index=hours)
    sh["high"] = sh["open"]
    ph = pd.DataFrame({"open": perp["open"].reindex(day_of).to_numpy()}, index=hours)
    ph["high"] = ph["open"] * 1.001
    for k, hi in (spikes or {}).items():
        ph.loc[idx[k] + pd.Timedelta(hours=5), "high"] = hi
    return spot, perp, sh, ph


def _weights(n, seed):
    rng = np.random.default_rng(seed)
    w, out = 1.0, []
    for _ in range(n):
        if rng.random() < 0.08:
            w = float(rng.choice([0.0, 0.5, 1.0]))
        out.append(w)
    return out


def _run(book, spot, perp, sh, ph, fd, weights, atr=1000.0):
    days = spot.index
    stamp = days[0] + pd.Timedelta(days=1)
    book.open(str(days[0].date()), weights[0], stamp, stamp, ["open"], atr)
    for k in range(1, len(days)):
        end = days[k] + pd.Timedelta(days=1)
        def cut(x, end=end):
            return x[x.index < end]
        _, problems = book.advance(cut(spot), cut(sh), cut(perp), cut(ph), fd, None)
        assert problems == []
        book.decide(str(days[k].date()), weights[k], end, end, ["d"], atr)
    return book


def _reference(spot, perp, fd, weights, cap_buys=True):
    """R15 ``simulate`` with R20 s = 1.0 targets (single cash, fills at the daily open). R15 itself
    lets cash dip below zero by the fee of a buy to target; ``cap_buys`` stops the buy at the cash."""
    cash, sq, pq, mark, prev = 10_000.0, 0.0, 0.0, np.nan, (np.nan, np.nan)
    eqs, trades = [], []
    for i, d in enumerate(spot.index[1:], 1):
        w = weights[i - 1]
        ts, tp = w + 0.5 * (1 - w), -0.5 * (1 - w)
        o, so = perp.at[d, "open"], spot.at[d, "open"]
        if pq:
            cash += pq * (o - mark)
        mark = o
        eq = cash + sq * so
        cur_s, cur_p = sq * so / eq, pq * o / eq
        for leg, cur, tgt, prv in (("perp", cur_p, tp, prev[1]), ("spot", cur_s, ts, prev[0])):
            if (tgt != prv or abs(cur - tgt) > max(0.2 * abs(tgt), 0.05)) and abs(cur - tgt) >= 1e-12:
                if leg == "perp":
                    dq = tgt * eq / o - pq
                    px = o * (1 + np.sign(dq) * 2e-4)
                    cash -= dq * (px - o) + abs(dq) * px * 0.0005
                    pq += dq
                else:
                    dq = tgt * eq / so - sq
                    px = so * (1 + np.sign(dq) * 2e-4)
                    if cap_buys and dq > 0:            # the book cannot spend cash it does not have
                        dq = min(dq, cash / (px * 1.001))
                    cash -= dq * px + abs(dq) * px * 0.001
                    sq += dq
                trades.append((str(d.date()), leg))
        prev = (ts, tp)
        c = perp.at[d, "close"]
        if pq:
            cash += pq * (c - mark)
        mark = c
        if pq and d in fd.index:
            cash -= pq * c * fd.at[d]
        eqs.append(cash + sq * spot.at[d, "close"])
    return eqs, trades


@pytest.mark.parametrize("seed", [1, 2])
def test_book_matches_the_backtest(seed):
    rng = np.random.default_rng(seed)
    prices = 30000 * np.exp(np.cumsum(rng.normal(0, 0.02, 240)))
    spot, perp, sh, ph = _market(prices)
    fd = pd.Series(rng.normal(0.0003, 0.0002, len(prices)), index=spot.index)
    weights = _weights(len(prices), seed)
    book = _run(NoStop(profile=LOOSE), spot, perp, sh, ph, fd, weights)
    eqs, ref_trades = _reference(spot, perp, fd, weights)
    got = [float(h["equity"]) for h in book.history]
    assert len(got) == len(eqs)
    assert np.allclose(got, eqs, rtol=1e-6, atol=0)
    raw, _ = _reference(spot, perp, fd, weights, cap_buys=False)       # R15 exactly: within fees
    assert np.allclose(got, raw, rtol=1e-3, atol=0)
    mine = sorted({(t["date"], t["leg"]) for t in book.trades if t["book"] == "paper"})
    assert mine == sorted(set(ref_trades))
    # wallets: every entry balances, transfers net to zero, no wallet goes below zero cash
    assert book.performance()["ledger_problems"] == []
    assert book.spot.bal("transfers") + book.perp.bal("transfers") == 0
    assert book.spot.bal("cash") >= 0 and book.perp.bal("cash") >= 0
    assert any(e.kind == "transfer" for e in book.perp.entries)


def test_short_receives_funding():
    prices = np.full(5, 30000.0)
    spot, perp, sh, ph = _market(prices, basis=0.0)
    fd = pd.Series(0.0003, index=spot.index)
    book = _run(NoStop(profile=LOOSE), spot, perp, sh, ph, fd, [0.0] * 5)
    q = -book.perp.position.qty
    assert q > 0
    paid = [e for e in book.perp.entries if e.kind == "funding"]
    assert len(paid) == 4
    for e in paid:
        assert Decimal(e.data["paid"]) == -q * Decimal("30000.0") * Decimal("0.0003")
    assert float(book.performance()["funding_received"]) > 0
    # delta neutral: equity moves only by funding and costs
    assert abs(float(book.history[-1]["net_exposure"])) < 1.0


def test_stop_closes_the_short_and_the_next_check_rehedges():
    prices = np.full(6, 30000.0)
    atr = 1000.0
    spot, perp, sh, ph = _market(prices, basis=0.0, spikes={2: 34000.0})
    fd = pd.Series(0.0, index=spot.index)
    book = _run(HedgeBook(profile=LOOSE), spot, perp, sh, ph, fd, [0.0] * 6, atr=atr)
    stops = [t for t in book.trades if t.get("reason") == "stop"]
    assert len(stops) == 1
    level = Decimal("30000.0") * (1 - Decimal("0.0002")) + 3 * Decimal("1000.0")
    assert stops[0]["date"] == str(spot.index[2].date())
    assert Decimal(stops[0]["price"]) == level * (1 + Decimal("0.0002"))
    assert Decimal(book.history[1]["perp_qty"]) == 0                       # unhedged after the stop (history starts on day 1)
    rehedge = [t for t in book.trades if t["leg"] == "perp" and t["date"] == str(spot.index[3].date())]
    assert rehedge and rehedge[0]["side"] == "SELL"                  # next check shorts again
    assert book.stop is not None and abs(Decimal(book.history[-1]["perp_weight"]) + Decimal("0.5")) < Decimal("0.001")
    liq = Decimal(book.history[-1]["liquidation_price"])
    assert liq > Decimal(book.stop["price"]) * Decimal("1.5")        # stop sits far below liquidation


def test_halt_closes_both_legs_and_ignores_later_decisions():
    prices = np.r_[np.full(3, 30000.0), 30000 * 0.70, np.full(4, 30000 * 0.70)]
    spot, perp, sh, ph = _market(prices, basis=0.0)
    fd = pd.Series(0.0, index=spot.index)
    days = spot.index
    book = HedgeBook()                                               # spot.v1: HALT at 25% drawdown
    stamp = days[0] + pd.Timedelta(days=1)
    book.open(str(days[0].date()), 1.0, stamp, stamp, ["open"], 1000.0)
    for k in range(1, len(days)):
        end = days[k] + pd.Timedelta(days=1)
        def cut(x, end=end):
            return x[x.index < end]
        book.advance(cut(spot), cut(sh), cut(perp), cut(ph), fd, None)
        book.decide(str(days[k].date()), 1.0 if k < 3 else 0.0, end, end, ["d"], 1000.0)
    assert book.risk.halted
    assert book.spot.position.qty == 0 and book.perp.position.qty == 0
    assert any(e["kind"] == "ignored_halted" for e in book.journal)
    assert book.performance()["ledger_problems"] == []


def test_state_round_trip_and_safety():
    prices = np.full(4, 30000.0)
    spot, perp, sh, ph = _market(prices)
    fd = pd.Series(0.0001, index=spot.index)
    book = _run(HedgeBook(), spot, perp, sh, ph, fd, [0.0, 0.0, 0.5, 1.0])
    s = json.loads(json.dumps(book.to_dict()))
    again = HedgeBook(s)
    assert again.to_dict() == book.to_dict()
    with pytest.raises(ValueError):
        PathABook(s)
    with pytest.raises(ValueError):
        BandSpotBook(s)
    with pytest.raises(ValueError):
        HedgeBook(BandSpotBook().to_dict())
    with pytest.raises(ValueError):
        HedgeBook(s, profile=LOOSE)
