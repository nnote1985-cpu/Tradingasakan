"""BAR_RESEARCH simulator — BUILDPLAN v4.2 §10 (bar profile) and §6.3 exits.

Event-driven loop over *execution bars* (1m preferred, 1h fallback). Signals come
from 1h closes; fills only use prices at or after the signal is available.

This is the research-spike simulator: float ledger, one portfolio, one position.
It is not the QUOTE_FORWARD simulator and does not claim execution precision.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Callable, Literal

import numpy as np
import pandas as pd

from trading.risk.sizing import (
    InstrumentRules, RiskProfile, SizingAllowances, plan_entry, round_take_profit,
)

Ambiguity = Literal["conservative", "optimistic"]
HaltPolicy = Literal["absorbing", "record_only"]


@dataclass(frozen=True)
class CostModel:
    """DEFAULT cost assumptions — verify against the actual fee tier (§4.1)."""
    taker_fee: float = 0.0005
    entry_slip_bps: float = 2.0
    exit_slip_bps: float = 2.0
    stop_slip_bps: float = 5.0

    def scaled(self, fee_mult: float = 1.0, slip_mult: float = 1.0) -> "CostModel":
        return CostModel(
            taker_fee=self.taker_fee * fee_mult,
            entry_slip_bps=self.entry_slip_bps * slip_mult,
            exit_slip_bps=self.exit_slip_bps * slip_mult,
            stop_slip_bps=self.stop_slip_bps * slip_mult,
        )


@dataclass(frozen=True)
class ExitParams:
    stop_atr_mult: float = 2.0
    take_profit_r: float = 2.0
    time_exit: pd.Timedelta = pd.Timedelta(hours=24)
    cooldown: pd.Timedelta = pd.Timedelta(hours=2)


@dataclass(frozen=True)
class BacktestConfig:
    initial_equity: float = 10_000.0
    costs: CostModel = field(default_factory=CostModel)
    exits: ExitParams = field(default_factory=ExitParams)
    profile: RiskProfile = field(default_factory=RiskProfile)
    rules: InstrumentRules = field(default_factory=InstrumentRules)
    allowances: SizingAllowances = field(default_factory=SizingAllowances)
    ambiguity: Ambiguity = "conservative"
    halt_policy: HaltPolicy = "absorbing"
    track_bar_equity: bool = True


@dataclass(frozen=True)
class Candidate:
    cand_id: str
    side: str
    available_at: pd.Timestamp
    atr: float
    exec_idx: int


@dataclass
class Position:
    cand_id: str
    side: str
    sign: int
    qty: float
    entry_price: float
    stop: float
    tp: float
    entry_idx: int
    entry_time: pd.Timestamp
    signal_time: pd.Timestamp
    initial_risk: float
    entry_fee: float
    funding: float = 0.0


@dataclass
class BacktestResult:
    trades: pd.DataFrame
    candidates: pd.DataFrame
    equity_close: pd.Series | None
    equity_worst: pd.Series | None
    daily_equity: pd.Series
    events: list[dict]
    stats: dict


def _ns(times) -> np.ndarray:
    """Epoch nanoseconds; pandas 3 may store datetimes in µs, so normalise."""
    return pd.DatetimeIndex(times).as_unit("ns").asi8


def build_candidates(h1: pd.DataFrame, exec_times: pd.DatetimeIndex) -> list[Candidate]:
    """Shared candidate stream; each maps to the first exec bar opening at/after
    the 1h close (the earliest executable price after the decision)."""
    sig = h1[h1["signal"] != "NO_TRADE"]
    if sig.empty:
        return []
    t_ns = _ns(exec_times)
    out = []
    for ts, row in sig.iterrows():
        avail = row["close_time"]
        idx = int(np.searchsorted(t_ns, avail.value, side="left"))
        out.append(Candidate(
            cand_id=f"{ts.isoformat()}:{row['signal']}", side=row["signal"],
            available_at=avail, atr=float(row["atr"]), exec_idx=idx,
        ))
    return out


class BarBacktester:
    def __init__(self, exec_bars: pd.DataFrame, h1: pd.DataFrame, h4: pd.DataFrame,
                 funding: pd.Series | None, config: BacktestConfig):
        self.cfg = config
        self.bars = exec_bars
        self.times = exec_bars.index
        self.t_ns = _ns(self.times)
        self.o = exec_bars["open"].to_numpy(float)
        self.h = exec_bars["high"].to_numpy(float)
        self.l = exec_bars["low"].to_numpy(float)
        self.c = exec_bars["close"].to_numpy(float)
        self.bar_len = (self.times[1] - self.times[0]) if len(self.times) > 1 else pd.Timedelta(minutes=1)
        self.candidates = build_candidates(h1, self.times)
        self.cand_at: dict[int, Candidate] = {}
        for cand in self.candidates:
            self.cand_at.setdefault(cand.exec_idx, cand)

        # Latest closed 4h close / EMA200 visible at each exec bar open.
        h4c = h4.dropna(subset=["close", "ema_slow"])
        k = np.searchsorted(_ns(h4c["close_time"]), self.t_ns, side="right") - 1
        valid_k = k >= 0
        self.h4_close = np.where(valid_k, h4c["close"].to_numpy()[np.clip(k, 0, None)], np.nan) if len(h4c) else np.full(len(self.t_ns), np.nan)
        self.h4_ema = np.where(valid_k, h4c["ema_slow"].to_numpy()[np.clip(k, 0, None)], np.nan) if len(h4c) else np.full(len(self.t_ns), np.nan)

        self.funding_at: dict[int, list[float]] = {}
        self.funding_supplied = funding is not None
        if funding is not None and len(funding):
            fidx = np.searchsorted(self.t_ns, _ns(funding.index), side="left")
            for i, rate in zip(fidx, funding.to_numpy(float)):
                if 0 <= i < len(self.t_ns):
                    self.funding_at.setdefault(int(i), []).append(rate)

    # ---- price helpers -------------------------------------------------
    def _adverse(self, price: float, sign: int, bps: float, opening: bool) -> float:
        """Adverse slippage: buys fill higher, sells fill lower."""
        buy = (sign > 0) if opening else (sign < 0)
        return price * (1 + bps / 1e4) if buy else price * (1 - bps / 1e4)

    def _exit_checks_intrabar(self, pos: Position, i: int) -> tuple[float, str, bool] | None:
        """Stop/TP inside bar i. Returns (fill, reason, ambiguous) or None."""
        cost = self.cfg.costs
        if pos.sign > 0:
            hit_sl = self.l[i] <= pos.stop
            hit_tp = self.h[i] >= pos.tp
        else:
            hit_sl = self.h[i] >= pos.stop
            hit_tp = self.l[i] <= pos.tp
        if not (hit_sl or hit_tp):
            return None
        ambiguous = hit_sl and hit_tp
        take_sl = hit_sl and (not hit_tp or self.cfg.ambiguity == "conservative")
        if take_sl:
            return self._adverse(pos.stop, pos.sign, cost.stop_slip_bps, opening=False), "STOP", ambiguous
        return self._adverse(pos.tp, pos.sign, cost.exit_slip_bps, opening=False), "TAKE_PROFIT", ambiguous

    def _open_exit_reason(self, pos: Position, i: int) -> str | None:
        """Exits decided before bar i opens: time exit and 4h trend exit (§6.3)."""
        if self.times[i] >= pos.entry_time + self.cfg.exits.time_exit:
            return "TIME_EXIT"
        c4, e4 = self.h4_close[i], self.h4_ema[i]
        if np.isfinite(c4) and np.isfinite(e4) and i > pos.entry_idx:
            if (pos.sign > 0 and c4 < e4) or (pos.sign < 0 and c4 > e4):
                return "TREND_EXIT"
        return None

    def _gap_exit(self, pos: Position, i: int) -> tuple[float, str] | None:
        """Bar opens beyond stop or TP: stop fills at the open (no price
        guarantee, §10.3); TP is capped at the target (conservative)."""
        o, cost = self.o[i], self.cfg.costs
        if (pos.sign > 0 and o <= pos.stop) or (pos.sign < 0 and o >= pos.stop):
            return self._adverse(o, pos.sign, cost.stop_slip_bps, opening=False), "STOP_GAP"
        if (pos.sign > 0 and o >= pos.tp) or (pos.sign < 0 and o <= pos.tp):
            return self._adverse(pos.tp, pos.sign, cost.exit_slip_bps, opening=False), "TAKE_PROFIT_GAP"
        return None

    # ---- main loop -----------------------------------------------------
    def run(self, *, start: pd.Timestamp | None = None, end: pd.Timestamp | None = None,
            filter_fn: Callable[[Candidate], bool] | None = None) -> BacktestResult:
        cfg = self.cfg
        n = len(self.t_ns)
        i0 = 0 if start is None else int(np.searchsorted(self.t_ns, pd.Timestamp(start).value, side="left"))
        i1 = n if end is None else int(np.searchsorted(self.t_ns, pd.Timestamp(end).value, side="left"))

        cash = cfg.initial_equity
        pos: Position | None = None
        halted = False
        pending_halt_flatten = False
        cooldown_until: pd.Timestamp | None = None
        hwm = cfg.initial_equity
        day = None
        day_start_equity = cash
        day_breached = False
        dd_in_breach = False

        trades: list[dict] = []
        cand_log: list[dict] = []
        events: list[dict] = []
        eq_close = np.full(i1 - i0, np.nan) if cfg.track_bar_equity else None
        eq_worst = np.full(i1 - i0, np.nan) if cfg.track_bar_equity else None
        daily_idx: list[pd.Timestamp] = []
        daily_val: list[float] = []
        incomplete_bars_in_position = 0
        last_equity = cash
        last_close = np.nan

        def close_position(i: int, fill: float, reason: str, at_open: bool, ambiguous: bool = False):
            nonlocal cash, pos, cooldown_until
            assert pos is not None
            gross = (fill - pos.entry_price) * pos.qty * pos.sign
            fee = fill * pos.qty * cfg.costs.taker_fee
            cash += gross - fee
            exit_time = self.times[i] if at_open else self.times[i] + self.bar_len
            net = gross - pos.entry_fee - fee + pos.funding
            trades.append({
                "cand_id": pos.cand_id, "side": pos.side, "signal_time": pos.signal_time,
                "entry_time": pos.entry_time, "entry_price": pos.entry_price, "qty": pos.qty,
                "stop": pos.stop, "take_profit": pos.tp, "exit_time": exit_time,
                "exit_price": fill, "exit_reason": reason, "ambiguous_bar": ambiguous,
                "gross_pnl": gross, "fees": pos.entry_fee + fee, "funding": pos.funding,
                "net_pnl": net, "initial_risk": pos.initial_risk,
                "r_multiple": net / pos.initial_risk if pos.initial_risk > 0 else np.nan,
                "hold_hours": (exit_time - pos.entry_time) / pd.Timedelta(hours=1),
            })
            cooldown_until = exit_time + cfg.exits.cooldown
            pos = None

        for j, i in enumerate(range(i0, i1)):
            t = self.times[i]
            if np.isnan(self.o[i]):
                if pos is not None:
                    incomplete_bars_in_position += 1
                if eq_close is not None:
                    eq_close[j] = last_equity
                    eq_worst[j] = last_equity
                continue

            d = t.normalize()
            if d != day:
                daily_idx.append(d)
                daily_val.append(last_equity)
                day = d
                day_start_equity = last_equity
                day_breached = False

            # Funding applies to a position held at the funding timestamp.
            if pos is not None and i in self.funding_at and pos.entry_idx < i:
                for rate in self.funding_at[i]:
                    pay = -pos.sign * pos.qty * self.o[i] * rate
                    pos.funding += pay
                    cash += pay

            # Exits decided before the open.
            if pos is not None:
                reason = "HALT_FLATTEN" if pending_halt_flatten else self._open_exit_reason(pos, i)
                if reason:
                    fill = self._adverse(self.o[i], pos.sign, cfg.costs.exit_slip_bps, opening=False)
                    close_position(i, fill, reason, at_open=True)
                else:
                    gap = self._gap_exit(pos, i)
                    if gap:
                        close_position(i, gap[0], gap[1], at_open=True)
            pending_halt_flatten = False

            # Entry.
            cand = self.cand_at.get(i)
            if cand is not None:
                status = None
                if halted:
                    status = "NOT_EVALUATED_HALTED"
                elif pos is not None:
                    status = "NOT_EVALUATED_POSITION_OPEN"
                elif cooldown_until is not None and t < cooldown_until:
                    status = "NOT_EVALUATED_COOLDOWN"
                elif filter_fn is not None and not filter_fn(cand):
                    status = "FILTER_VETO"
                if status is None:
                    sign = 1 if cand.side == "LONG" else -1
                    est = self._adverse(self.o[i], sign, cfg.costs.entry_slip_bps, opening=True)
                    equity_now = cash
                    day_loss = max(0.0, day_start_equity - equity_now)
                    remaining = max(0.0, day_start_equity * float(cfg.profile.daily_loss_trigger_fraction) - day_loss)
                    plan = plan_entry(
                        side=cand.side, entry_estimate=est, atr=cand.atr,
                        stop_atr_mult=cfg.exits.stop_atr_mult, equity=equity_now,
                        remaining_day_budget=remaining, profile=cfg.profile,
                        rules=cfg.rules, allow=cfg.allowances,
                    )
                    if "reject" in plan:
                        status = plan["reject"]
                    else:
                        qty = float(plan["qty"])
                        stop = float(plan["stop"])
                        risk_px = abs(est - stop)
                        tp_raw = est + sign * cfg.exits.take_profit_r * risk_px
                        tp = float(round_take_profit(Decimal(str(float(tp_raw))), cfg.rules.tick, cand.side))
                        fee = est * qty * cfg.costs.taker_fee
                        cash -= fee
                        pos = Position(
                            cand_id=cand.cand_id, side=cand.side, sign=sign, qty=qty,
                            entry_price=est, stop=stop, tp=tp, entry_idx=i, entry_time=t,
                            signal_time=cand.available_at, initial_risk=qty * risk_px,
                            entry_fee=fee,
                        )
                        status = "OPENED"
                cand_log.append({"cand_id": cand.cand_id, "side": cand.side,
                                 "available_at": cand.available_at, "status": status})

            # Intrabar stop / take profit.
            if pos is not None:
                hit = self._exit_checks_intrabar(pos, i)
                if hit:
                    close_position(i, hit[0], hit[1], at_open=False, ambiguous=hit[2])

            # Mark to market.
            unreal = 0.0
            worst = 0.0
            if pos is not None:
                unreal = (self.c[i] - pos.entry_price) * pos.qty * pos.sign
                adverse_px = self.l[i] if pos.sign > 0 else self.h[i]
                worst = (adverse_px - pos.entry_price) * pos.qty * pos.sign
            last_equity = cash + unreal
            last_close = self.c[i]
            if eq_close is not None:
                eq_close[j] = last_equity
                eq_worst[j] = cash + min(unreal, worst)

            # Loss limits (§9.5) evaluated on the close mark.
            hwm = max(hwm, last_equity)
            daily_hit = last_equity <= day_start_equity * (1 - float(cfg.profile.daily_loss_trigger_fraction))
            dd_hit = last_equity <= hwm * (1 - float(cfg.profile.portfolio_drawdown_halt_fraction))
            new_daily = daily_hit and not day_breached
            new_dd = dd_hit and not dd_in_breach
            dd_in_breach = dd_hit
            if new_daily or new_dd:
                kind = "DAILY_LOSS" if new_daily else "DRAWDOWN"
                if new_daily:
                    day_breached = True
                events.append({"time": t, "event": f"BREACH_{kind}", "equity": last_equity,
                               "hwm": hwm, "day_start_equity": day_start_equity,
                               "policy": cfg.halt_policy})
                if cfg.halt_policy == "absorbing" and not halted:
                    halted = True
                    if pos is not None:
                        pending_halt_flatten = True

        if pos is not None:
            last_valid = i1 - 1
            while last_valid > i0 and np.isnan(self.c[last_valid]):
                last_valid -= 1
            fill = self._adverse(self.c[last_valid], pos.sign, cfg.costs.exit_slip_bps, opening=False)
            close_position(last_valid, fill, "END_OF_DATA", at_open=False)
            last_equity = cash
        if day is not None:
            daily_idx.append(day + pd.Timedelta(days=1))
            daily_val.append(last_equity)

        idx = self.times[i0:i1]
        stats = {
            "incomplete_bars_in_position": incomplete_bars_in_position,
            "funding_supplied": self.funding_supplied,
            "halted": halted,
            "final_equity": last_equity,
            "start": idx[0] if len(idx) else None,
            "end": idx[-1] if len(idx) else None,
            "last_close": last_close,
        }
        return BacktestResult(
            trades=pd.DataFrame(trades),
            candidates=pd.DataFrame(cand_log),
            equity_close=pd.Series(eq_close, index=idx) if eq_close is not None else None,
            equity_worst=pd.Series(eq_worst, index=idx) if eq_worst is not None else None,
            daily_equity=pd.Series(daily_val, index=pd.DatetimeIndex(daily_idx), name="equity"),
            events=events,
            stats=stats,
        )

    def standalone_outcomes(self, *, start=None, end=None) -> pd.DataFrame:
        """Outcome (R multiple) of every candidate as if taken alone with fresh
        equity — labels for ORACLE_SYNTHETIC power simulation only (§17.3)."""
        rows = []
        cfg = replace(self.cfg, halt_policy="record_only", track_bar_equity=False)
        lo = -np.inf if start is None else pd.Timestamp(start).value
        hi = np.inf if end is None else pd.Timestamp(end).value
        sub = BarBacktester.__new__(BarBacktester)
        sub.__dict__.update(self.__dict__)
        sub.cfg = cfg
        horizon = int(np.ceil((cfg.exits.time_exit + 2 * self.bar_len) / self.bar_len))
        for cand in self.candidates:
            if not (lo <= cand.available_at.value < hi) or cand.exec_idx >= len(self.t_ns):
                continue
            sub.cand_at = {cand.exec_idx: cand}
            s = self.times[cand.exec_idx]
            e_idx = min(len(self.t_ns), cand.exec_idx + horizon)
            res = sub.run(start=s, end=self.times[e_idx - 1] + self.bar_len)
            if len(res.trades):
                tr = res.trades.iloc[0]
                rows.append({"cand_id": cand.cand_id, "side": cand.side,
                             "available_at": cand.available_at, "r_multiple": tr["r_multiple"],
                             "net_pnl": tr["net_pnl"], "exit_reason": tr["exit_reason"]})
            else:
                rows.append({"cand_id": cand.cand_id, "side": cand.side,
                             "available_at": cand.available_at, "r_multiple": np.nan,
                             "net_pnl": np.nan, "exit_reason": res.candidates["status"].iloc[0] if len(res.candidates) else "NO_BAR"})
        return pd.DataFrame(rows)
