"""Shadow portfolio for trend_breakout_v1.0 (BUILDPLAN v4.2 §6, §9, §10 BAR_RESEARCH, §11).

Simulated money only: there is no order route. Signals come from
``trend_breakout_v1.build_features``; every candidate gets a ``decision_at``, and
its entry may fill only at the open of the first execution bar opening at or
after ``eligible_fill_at = decision_at + validation_delay + network_delay``.

Money goes through :class:`ShadowLedger` (Decimal journal) and loss limits
through :class:`RiskState` (durable HALT). The book saves to a dict and resumes
where it stopped, so a run split in two equals one unbroken run.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import ROUND_FLOOR, Decimal

import numpy as np
import pandas as pd

from trading.risk.sizing import InstrumentRules, RiskProfile, SizingAllowances, plan_entry, round_take_profit
from trading.shadow.ledger import ShadowLedger
from trading.shadow.risk_state import RiskState
from trading.shadow.spot_ledger import SpotLedger
from trading.simulator.bar_backtest import CostModel, ExitParams
from trading.strategies.trend_breakout_v1 import STRATEGY_ID

BOOK_VERSION = "shadow_book.v1"


def D(x) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


@dataclass(frozen=True)
class ShadowConfig:
    initial_equity: Decimal = Decimal("10000")
    costs: CostModel = field(default_factory=CostModel)
    exits: ExitParams = field(default_factory=ExitParams)
    profile: RiskProfile = field(default_factory=RiskProfile)
    rules: InstrumentRules = field(default_factory=InstrumentRules)
    allowances: SizingAllowances = field(default_factory=SizingAllowances)
    decision_delay: pd.Timedelta = pd.Timedelta(seconds=1)      # signal available -> decision done
    validation_delay: pd.Timedelta = pd.Timedelta(0)
    network_delay: pd.Timedelta = pd.Timedelta(milliseconds=100)
    instrument_type: str = "PERP_FUTURE"                         # or "SPOT": long only, cash-funded, no funding
    daily_operating_cost: Decimal = Decimal(0)                  # USDT-equivalent, booked as each UTC day ends

    def describe(self) -> dict:
        return {"strategy": STRATEGY_ID, "simulator": "BAR_RESEARCH", "book": BOOK_VERSION,
                "initial_equity": str(self.initial_equity), "costs": asdict(self.costs),
                "exits": {k: str(v) for k, v in asdict(self.exits).items()},
                "profile": {k: str(v) for k, v in asdict(self.profile).items()},
                "rules": {k: str(v) for k, v in asdict(self.rules).items()},
                "allowances": {k: str(v) for k, v in asdict(self.allowances).items()},
                "decision_delay": str(self.decision_delay), "validation_delay": str(self.validation_delay),
                "network_delay": str(self.network_delay), "daily_operating_cost": str(self.daily_operating_cost),
                "instrument_type": self.instrument_type,
                "halt_followup": "ABSORBING_UNTIL_ENDPOINT"}


def _iso(t) -> str:
    return pd.Timestamp(t).isoformat()


LEDGERS: dict[str, type[ShadowLedger]] = {"PERP_FUTURE": ShadowLedger, "SPOT": SpotLedger}


class ShadowPortfolio:
    def __init__(self, config: ShadowConfig = ShadowConfig(), state: dict | None = None):
        self.cfg = config
        if config.instrument_type not in LEDGERS:
            raise ValueError(f"instrument type {config.instrument_type} is not supported by the shadow book")
        ledger_cls = LEDGERS[config.instrument_type]
        if state is None:
            self.ledger = ledger_cls()
            self.risk = RiskState()
            self.pos: dict | None = None
            self.cooldown_until: str | None = None
            self.halt_flatten_pending = False
            self.last_bar: str | None = None
            self.last_close: str | None = None
            self.trades: list[dict] = []
            self.candidates: list[dict] = []
            self.events: list[dict] = []
            self.daily: list[dict] = []
            self.incomplete_bars_in_position = 0
        else:
            if state.get("book") != BOOK_VERSION or state.get("config") != config.describe():
                raise ValueError("state belongs to another book version or config")
            self.ledger = ledger_cls.from_dict(state["ledger"])
            self.risk = RiskState.from_dict(state["risk"])
            for k in ("pos", "cooldown_until", "halt_flatten_pending", "last_bar", "last_close", "trades",
                      "candidates", "events", "daily", "incomplete_bars_in_position"):
                setattr(self, k, state[k])

    def to_dict(self) -> dict:
        return {"book": BOOK_VERSION, "config": self.cfg.describe(), "ledger": self.ledger.to_dict(),
                "risk": self.risk.to_dict(), "pos": self.pos, "cooldown_until": self.cooldown_until,
                "halt_flatten_pending": self.halt_flatten_pending, "last_bar": self.last_bar,
                "last_close": self.last_close, "trades": self.trades, "candidates": self.candidates,
                "events": self.events, "daily": self.daily,
                "incomplete_bars_in_position": self.incomplete_bars_in_position}

    # ---- helpers -----------------------------------------------------------
    def _mark(self) -> Decimal:
        return D(self.last_close) if self.last_close is not None else Decimal(1)

    def equity(self) -> tuple[Decimal, Decimal]:
        m = self._mark()
        return self.ledger.trading_equity(m), self.ledger.economic_equity(m)

    @staticmethod
    def _adverse(price: float, sign: int, bps: float, opening: bool) -> float:
        buy = (sign > 0) if opening else (sign < 0)
        return price * (1 + bps / 1e4) if buy else price * (1 - bps / 1e4)

    def _fill(self, key: str, t, sign_of_trade: int, qty: Decimal, price: float, reference: float) -> Decimal:
        px = D(price)
        fee = px * qty * D(self.cfg.costs.taker_fee)
        self.ledger.fill(key, t, "BUY" if sign_of_trade > 0 else "SELL", qty, px, fee, reference_price=reference)
        return fee

    def _close(self, t: pd.Timestamp, fill: float, reference: float, reason: str, ambiguous: bool = False) -> None:
        p = self.pos
        assert p is not None
        qty = D(p["qty"])
        realized_before = self.ledger.bal("realized_pnl")
        fee = self._fill(f"{p['cand_id']}:exit", t, -p["sign"], qty, fill, reference)
        gross = -(self.ledger.bal("realized_pnl") - realized_before)
        funding = D(p["funding"])
        fees = D(p["entry_fee"]) + fee
        net = gross - fees - funding
        risk0 = D(p["initial_risk"])
        self.trades.append({
            "cand_id": p["cand_id"], "side": p["side"], "decision_at": p["decision_at"],
            "eligible_fill_at": p["eligible_fill_at"], "entry_time": p["entry_time"], "entry_price": p["entry_price"],
            "qty": p["qty"], "stop": p["stop"], "take_profit": p["take_profit"], "exit_time": _iso(t),
            "exit_price": str(D(fill)), "exit_reason": reason, "ambiguous_bar": ambiguous,
            "gross_pnl": str(gross), "fees": str(fees), "funding_paid": str(funding), "net_pnl": str(net),
            "initial_risk": str(risk0), "r_multiple": str(net / risk0) if risk0 > 0 else None})
        self.cooldown_until = _iso(t + self.cfg.exits.cooldown)
        self.pos = None

    def open_risk_to_stop(self) -> Decimal:
        """Remaining loss to the stop from the current mark (§9.5), 0 when flat."""
        if self.pos is None:
            return Decimal(0)
        p = self.pos
        loss = (self._mark() - D(p["stop"])) * D(p["qty"]) * p["sign"]
        return max(Decimal(0), loss)

    # ---- main loop ---------------------------------------------------------
    def run(self, exec_bars: pd.DataFrame, h1: pd.DataFrame, h4: pd.DataFrame,
            funding: pd.Series | None = None) -> None:
        """Process every execution bar after ``last_bar``. ``exec_bars`` must be on
        a regular grid (missing bars as NaN rows); ``h1``/``h4`` come from
        ``trend_breakout_v1.build_features`` on data available to this run."""
        cfg = self.cfg
        if self.last_bar is None and not self.ledger.entries:
            first = exec_bars.index[0]
            self.ledger.open("open", first, cfg.initial_equity)
            self.events.append({"time": _iso(first), "event": "OPEN", "config": cfg.describe()})
        start = pd.Timestamp(self.last_bar) if self.last_bar else None
        bars = exec_bars[exec_bars.index > start] if start is not None else exec_bars
        if bars.empty:
            return
        bar_len = (exec_bars.index[1] - exec_bars.index[0]) if len(exec_bars) > 1 else pd.Timedelta(hours=1)

        # Candidates whose entry bar falls in this run.
        sig = h1[h1["signal"] != "NO_TRADE"]
        cand_at: dict[pd.Timestamp, dict] = {}
        for ts, row in sig.iterrows():
            decision_at = row["close_time"] + cfg.decision_delay
            eligible = decision_at + cfg.validation_delay + cfg.network_delay
            if start is not None and eligible <= start:
                continue
            j = bars.index.searchsorted(eligible, side="left")
            if j >= len(bars):
                continue
            cand_at.setdefault(bars.index[j], {
                "cand_id": f"{ts.isoformat()}:{row['signal']}", "side": row["signal"], "atr": float(row["atr"]),
                "signal_close": _iso(row["close_time"]), "decision_at": _iso(decision_at),
                "eligible_fill_at": _iso(eligible)})

        h4c = h4.dropna(subset=["close", "ema_slow"])
        h4_close_t = h4c["close_time"].to_numpy() if len(h4c) else np.array([], dtype="datetime64[ns]")
        fund_at: dict[pd.Timestamp, list[tuple[str, float]]] = {}
        if funding is not None:
            for ft, rate in funding.items():
                j = bars.index.searchsorted(ft, side="left")
                if j < len(bars):
                    fund_at.setdefault(bars.index[j], []).append((_iso(ft), float(rate)))

        for t, bar in bars.iterrows():
            o, h, lo, c = (float(bar[k]) for k in ("open", "high", "low", "close"))
            if not np.isfinite(o):
                if self.pos is not None:
                    self.incomplete_bars_in_position += 1
                self.last_bar = _iso(t)
                continue

            prev_day = self.risk.day
            if prev_day is not None and prev_day != str(t.date()) and cfg.daily_operating_cost > 0:
                # The allocated cost of a completed UTC day, booked when the day ends.
                self.ledger.operating_cost(f"opcost:{prev_day}", t, cfg.daily_operating_cost, "allocated daily")
            tr_eq, ec_eq = self.equity()
            if self.risk.start_day(t, tr_eq, ec_eq):
                self.daily.append({"date": str(t.date()), "trading_equity_start": str(tr_eq),
                                   "economic_equity_start": str(ec_eq), "halted": self.risk.halted})

            if self.pos is not None and t in fund_at and pd.Timestamp(self.pos["entry_time"]) < t:
                for fid, rate in fund_at[t]:
                    before = self.ledger.bal("funding")
                    self.ledger.funding(f"funding:{fid}", t, rate, o)
                    self.pos["funding"] = str(D(self.pos["funding"]) + self.ledger.bal("funding") - before)

            # Exits decided before the open.
            if self.pos is not None:
                p = self.pos
                reason = "HALT_FLATTEN" if self.halt_flatten_pending else None
                if reason is None and t >= pd.Timestamp(p["entry_time"]) + cfg.exits.time_exit:
                    reason = "TIME_EXIT"
                if reason is None and len(h4_close_t) and t > pd.Timestamp(p["entry_time"]):
                    k = int(np.searchsorted(h4_close_t, t.to_datetime64(), side="right")) - 1
                    if k >= 0:
                        c4, e4 = float(h4c["close"].iloc[k]), float(h4c["ema_slow"].iloc[k])
                        if (p["sign"] > 0 and c4 < e4) or (p["sign"] < 0 and c4 > e4):
                            reason = "TREND_EXIT"
                if reason:
                    self._close(t, self._adverse(o, p["sign"], cfg.costs.exit_slip_bps, False), o, reason)
                else:
                    stop, tp = float(p["stop"]), float(p["take_profit"])
                    if (p["sign"] > 0 and o <= stop) or (p["sign"] < 0 and o >= stop):
                        self._close(t, self._adverse(o, p["sign"], cfg.costs.stop_slip_bps, False), o, "STOP_GAP")
                    elif (p["sign"] > 0 and o >= tp) or (p["sign"] < 0 and o <= tp):
                        self._close(t, self._adverse(tp, p["sign"], cfg.costs.exit_slip_bps, False), tp, "TAKE_PROFIT_GAP")
            self.halt_flatten_pending = False

            cand = cand_at.get(t)
            if cand is not None:
                status = self._try_enter(cand, t, o)
                self.candidates.append({**{k: cand[k] for k in ("cand_id", "side", "decision_at", "eligible_fill_at")},
                                        "entry_bar": _iso(t), "status": status})

            if self.pos is not None:
                p = self.pos
                stop, tp = float(p["stop"]), float(p["take_profit"])
                hit_sl = lo <= stop if p["sign"] > 0 else h >= stop
                hit_tp = h >= tp if p["sign"] > 0 else lo <= tp
                if hit_sl:                     # conservative: SL first when both touch (§10.3)
                    self._close(t + bar_len, self._adverse(stop, p["sign"], cfg.costs.stop_slip_bps, False), stop,
                                "STOP", ambiguous=hit_tp)
                elif hit_tp:
                    self._close(t + bar_len, self._adverse(tp, p["sign"], cfg.costs.exit_slip_bps, False), tp,
                                "TAKE_PROFIT")

            self.last_close = str(D(c))
            self.last_bar = _iso(t)
            tr_eq, ec_eq = self.equity()
            reason = self.risk.observe(t, tr_eq, ec_eq, cfg.profile)
            if reason:
                self.events.append({"time": _iso(t), "event": f"HALT_{reason}", "trading_equity": str(tr_eq),
                                    "economic_equity": str(ec_eq), "high_water_mark": str(self.risk.high_water_mark),
                                    "day_start_trading": str(self.risk.day_start_trading),
                                    "day_start_economic": str(self.risk.day_start_economic)})
                if self.pos is not None:
                    self.halt_flatten_pending = True

    def _try_enter(self, cand: dict, t: pd.Timestamp, o: float) -> str:
        cfg = self.cfg
        if self.risk.halted:
            return "NOT_EVALUATED_HALTED"
        if self.pos is not None:
            return "NOT_EVALUATED_POSITION_OPEN"
        if self.cooldown_until is not None and t < pd.Timestamp(self.cooldown_until):
            return "NOT_EVALUATED_COOLDOWN"
        spot = cfg.instrument_type == "SPOT"
        if spot and cand["side"] != "LONG":
            return "REJECT_NO_SHORT_ON_SPOT"
        sign = 1 if cand["side"] == "LONG" else -1
        est = self._adverse(o, sign, cfg.costs.entry_slip_bps, True)
        tr_eq, ec_eq = self.equity()
        remaining = self.risk.remaining_day_budget(tr_eq, self.open_risk_to_stop(), cfg.profile)
        plan = plan_entry(side=cand["side"], entry_estimate=est, atr=cand["atr"],
                          stop_atr_mult=cfg.exits.stop_atr_mult, equity=max(Decimal(0), min(tr_eq, ec_eq)),
                          remaining_day_budget=remaining, profile=cfg.profile, rules=cfg.rules, allow=cfg.allowances)
        if "reject" in plan:
            return plan["reject"]
        qty, stop = plan["qty"], plan["stop"]
        if spot:   # pay cost and fee from cash: floor to the step, never round up
            afford = self.ledger.bal("cash") / (D(est) * (1 + D(cfg.costs.taker_fee)))
            step = cfg.rules.qty_step
            qty = min(qty, (afford / step).to_integral_value(rounding=ROUND_FLOOR) * step)
            if qty <= 0 or qty * D(est) < cfg.rules.min_notional:
                return "REJECT_INSUFFICIENT_CASH"
        fee = self._fill(f"{cand['cand_id']}:entry", t, sign, qty, est, o)
        fill = D(est)
        risk_px = abs(fill - stop)
        if cfg.exits.take_profit_r is None:
            tp = "Infinity" if sign > 0 else "-Infinity"
        else:   # 2x the actual initial price risk from the fill (§6.3)
            tp = str(round_take_profit(fill + sign * D(cfg.exits.take_profit_r) * risk_px, cfg.rules.tick, cand["side"]))
        self.pos = {"cand_id": cand["cand_id"], "side": cand["side"], "sign": sign, "qty": str(qty),
                    "entry_price": str(fill), "stop": str(stop), "take_profit": tp, "entry_time": _iso(t),
                    "decision_at": cand["decision_at"], "eligible_fill_at": cand["eligible_fill_at"],
                    "initial_risk": str(qty * risk_px), "entry_fee": str(fee), "funding": "0"}
        return "OPENED"

    def summary(self) -> dict:
        tr, ec = self.equity()
        return {"strategy": STRATEGY_ID, "last_bar": self.last_bar, "trading_equity": str(tr),
                "economic_equity": str(ec), "operating_costs": str(self.ledger.bal("operating_costs")),
                "high_water_mark": str(self.risk.high_water_mark), "halted": self.risk.halted,
                "halt_reason": self.risk.halt_reason, "halted_at": self.risk.halted_at,
                "position": None if self.pos is None else {k: self.pos[k] for k in ("side", "qty", "entry_price", "stop", "take_profit")},
                "trades": len(self.trades), "incomplete_bars_in_position": self.incomplete_bars_in_position,
                "ledger_problems": self.ledger.check()}
