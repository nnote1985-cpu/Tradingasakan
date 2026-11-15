"""tn-r10a-code-v2: the R10a rule on the strategy contract (docs/m3-strategy-core-plan.md §3.3).

The rule is the one of tn-r10a-code-v1.1 (``trading.runtime.r10a.decide``), unchanged:

- trend = ±2% band latch around the perp's SMA100 (in above SMA100 × 1.02, out below × 0.98);
- funding factor = 0.5 when the 7-day mean daily funding is above 0.0009, otherwise 1;
- target BTC weight = trend × funding factor (0, 0.5 or 1);
- wait while the decided day's close or funding (print or premium-index proxy) is not out;
- a daily close the archive skipped before the decided day is filled from the futures REST API and
  listed in the snapshot (``rest_filled_days``).

The snapshot has the same keys and values as v1.1, so the same inputs give the same
``inputs_sha256``; the replay test checks this on the v1.1 soak (plan §4.1). Only the version
(``tn-r10a-code-v2``) and the shape of the answer (a :class:`TargetDecision`) differ. Sizing is not
here: the order planner does it.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pandas as pd

from trading.contracts.strategy import Market, StrategyInputs, TargetDecision

VERSION = "tn-r10a-code-v2"
SYMBOL = "BTCUSDT"
HISTORY_DAYS = 400                          # SMA100 needs 100 closes; v1.1 and the Shadow book load 400
FUNDING_DAYS = 40


def _num(x: Any) -> float | None:
    return None if x is None or x != x else float(x)


class R10aV2:
    version = VERSION
    instrument = SYMBOL
    market = Market.SPOT

    def load(self, day: date) -> StrategyInputs:
        """Daily perp klines (archive gaps filled from REST), daily funding and its proxy flags."""
        from trading.alerts.live_data import daily_funding, daily_klines
        from trading.runtime.r10a import fill_gaps, rest_daily_klines

        daily = daily_klines(SYMBOL, day - timedelta(days=HISTORY_DAYS), day)
        daily, filled = fill_gaps(daily, day, rest_daily_klines)
        funding, est = daily_funding(SYMBOL, day - timedelta(days=FUNDING_DAYS), day)
        return StrategyInputs(bars=daily, series={"funding": funding, "funding_est": est},
                              extra={"rest_filled_days": filled})

    def decide(self, inputs: StrategyInputs, day: date, data_ready_at: datetime) -> TargetDecision | None:
        from trading.alerts.live_data import FUNDING_POLICY
        from trading.alerts.risk_check import missing_inputs, signals
        from trading.strategies.sma_band import BAND, band_state

        daily: pd.DataFrame = inputs.bars
        funding: pd.Series = inputs.series["funding"]
        est: pd.Series = inputs.series["funding_est"]
        rest_filled = list(inputs.extra.get("rest_filled_days") or [])

        sig = signals(daily, funding)
        if not len(sig) or sig.index[-1].date() < day:
            return None                                     # the archive does not have the day yet
        sig = sig[sig.index <= pd.Timestamp(day, tz="UTC")]
        state = band_state(sig["close"], signed=False)
        fund = sig["funding_hot"].map({True: 0.5, False: 1.0}).astype(float)
        w = state.iloc[-1] * fund.iloc[-1]
        t = sig.iloc[-1]
        last = sig.index[-1]
        window = []
        for i in range(6, -1, -1):
            d = last - timedelta(days=i)
            window.append({"day": str(d.date()), "funding": _num(funding.get(d)),
                           "source": "proxy" if bool(est.get(d, False)) else ("actual" if d in funding.index else "missing")})
        if window[-1]["source"] == "missing":
            return None                                     # the day's funding is not out yet: wait
        proxy = sum(1 for x in window if x["source"] == "proxy")
        hot = t["funding_hot"]
        snap: dict[str, Any] = {
            "close_perp": _num(t["close"]), "sma100": _num(t["sma100"]), "band": BAND, "band_state": _num(state.iloc[-1]),
            "funding_7d_per_day": _num(t["funding_7d_per_day"]),
            "funding_hot": None if hot is None or hot != hot else bool(hot), "suggested_btc_weight": _num(w),
            "funding_window": window, "funding_policy": FUNDING_POLICY["version"], "funding_proxy_days": proxy}
        if rest_filled:
            snap["rest_filled_days"] = rest_filled
        weight: Decimal | None
        if w != w:
            weight, reason = None, ", ".join(missing_inputs(sig)) or "inputs missing"
        else:
            weight = Decimal(str(float(w)))
            sma = float(t["sma100"])
            reason = (f"close {float(t['close']):,.2f}, SMA100 {sma:,.2f}: in above {sma * (1 + BAND):,.2f}, out below "
                      f"{sma * (1 - BAND):,.2f}; {'holding' if state.iloc[-1] == 1 else 'out'}; funding x{fund.iloc[-1]:g}")
            if proxy:
                reason += f"; funding {proxy}/7 days are premium-index estimates"
        return TargetDecision.make(strategy_version=self.version, instrument=self.instrument, market=self.market, day=day,
                                   target_weight=weight, reason=reason, inputs=snap, data_ready_at=data_ready_at)
