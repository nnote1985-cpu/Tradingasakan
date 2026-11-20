"""Turn Futures Demo REST answers into the rows the desk stores (futures-desk-plan.md §4).

Pure functions: no network, no database. Numbers stay as Decimal strings so
nothing is rounded on the way to Postgres.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from trading.futures import rules


def _d(v: Any, default: str = "0") -> Decimal:
    if v is None or v == "":
        return Decimal(default)
    return Decimal(str(v))


def _ms(v: Any) -> datetime:
    return datetime.fromtimestamp(int(v) / 1000, tz=UTC)


def _s(v: Decimal) -> str:
    """Plain decimal text without exponent or trailing zeros."""
    t = format(v.normalize(), "f")
    return "0" if t in ("-0", "") else t


@dataclass(frozen=True)
class Snapshot:
    synced_at: datetime
    wallet_balance: Decimal
    margin_balance: Decimal        # equity: wallet + unrealized P&L
    available_balance: Decimal
    unrealized_pnl: Decimal
    positions: list[dict[str, Any]] = field(default_factory=list)
    open_orders: list[dict[str, Any]] = field(default_factory=list)
    markets: dict[str, dict[str, Any]] = field(default_factory=dict)


def markets(exchange_info: Any, premium: Any, symbols: tuple[str, ...] = rules.SYMBOLS) -> dict[str, dict[str, Any]]:
    """Mark price and the order filters the desk needs, for the symbols it trades."""
    marks = {p["symbol"]: p for p in (premium if isinstance(premium, list) else [premium]) if isinstance(p, dict)}
    out: dict[str, dict[str, Any]] = {}
    for s in (exchange_info or {}).get("symbols", []):
        sym = s.get("symbol")
        if sym not in symbols:
            continue
        f = {x["filterType"]: x for x in s.get("filters", [])}
        m = marks.get(sym, {})
        out[sym] = {
            "mark": _s(_d(m.get("markPrice"))),
            "funding_rate": _s(_d(m.get("lastFundingRate"))),
            "next_funding": _ms(m["nextFundingTime"]).isoformat() if m.get("nextFundingTime") else None,
            "tick_size": _s(_d(f.get("PRICE_FILTER", {}).get("tickSize"))),
            "step_size": _s(_d(f.get("MARKET_LOT_SIZE", f.get("LOT_SIZE", {})).get("stepSize"))),
            "min_qty": _s(_d(f.get("MARKET_LOT_SIZE", f.get("LOT_SIZE", {})).get("minQty"))),
            "min_notional": _s(_d(f.get("MIN_NOTIONAL", {}).get("notional"))),
        }
    return out


def positions(position_risk: Any, configs: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Open positions only (one-way mode: positionSide BOTH; the sign of the amount gives the side)."""
    out = []
    for p in position_risk or []:
        amt = _d(p.get("positionAmt"))
        if amt == 0:
            continue
        sym = p["symbol"]
        cfg = (configs or {}).get(sym, {})
        isolated = _d(p.get("isolatedWallet")) > 0 or str(cfg.get("marginType", "")).upper() == "ISOLATED"
        liq = _d(p.get("liquidationPrice"))
        out.append({
            "symbol": sym,
            "side": "LONG" if amt > 0 else "SHORT",
            "qty": _s(abs(amt)),
            "entry_price": _s(_d(p.get("entryPrice"))),
            "break_even_price": _s(_d(p.get("breakEvenPrice"))),
            "mark_price": _s(_d(p.get("markPrice"))),
            "liquidation_price": _s(liq) if liq > 0 else None,
            "unrealized_pnl": _s(_d(p.get("unRealizedProfit"))),
            "notional": _s(abs(_d(p.get("notional")))),
            "margin": _s(_d(p.get("isolatedMargin")) if isolated else _d(p.get("initialMargin"))),
            "margin_type": "ISOLATED" if isolated else "CROSS",
            "leverage": int(cfg["leverage"]) if cfg.get("leverage") is not None else None,
            "updated_at": _ms(p["updateTime"]).isoformat() if p.get("updateTime") else None,
        })
    return sorted(out, key=lambda x: x["symbol"])


def _kind(order_type: str, reduce: bool) -> str:
    t = order_type.upper()
    if t in ("STOP_MARKET", "STOP"):
        return "SL"
    if t in ("TAKE_PROFIT_MARKET", "TAKE_PROFIT"):
        return "TP"
    if t == "TRAILING_STOP_MARKET":
        return "TRAILING"
    return "CLOSE" if reduce else "ENTRY"


def open_orders(orders: Any, algo_orders: Any) -> list[dict[str, Any]]:
    """Regular open orders plus open conditional (algo) orders, each tagged SL / TP / ENTRY / CLOSE / TRAILING."""
    out = []
    for o in orders or []:
        typ = str(o.get("type") or o.get("origType") or "")
        reduce = bool(o.get("reduceOnly")) or bool(o.get("closePosition"))
        stop = _d(o.get("stopPrice"))
        out.append({
            "source": "order", "id": str(o.get("orderId")), "symbol": o.get("symbol"), "side": o.get("side"), "type": typ,
            "kind": _kind(typ, reduce), "price": _s(_d(o.get("price"))), "trigger_price": _s(stop) if stop > 0 else None,
            "qty": _s(_d(o.get("origQty"))), "close_position": bool(o.get("closePosition")), "reduce_only": bool(o.get("reduceOnly")),
            "created_at": _ms(o["time"]).isoformat() if o.get("time") else None,
        })
    algos = algo_orders.get("orders", []) if isinstance(algo_orders, dict) else (algo_orders or [])
    for o in algos:
        typ = str(o.get("orderType") or o.get("type") or "")
        reduce = bool(o.get("reduceOnly")) or bool(o.get("closePosition"))
        trig = _d(o.get("triggerPrice") or o.get("stopPrice"))
        out.append({
            "source": "algo", "id": str(o.get("algoId")), "symbol": o.get("symbol"), "side": o.get("side"), "type": typ,
            "kind": _kind(typ, reduce), "price": _s(_d(o.get("price"))), "trigger_price": _s(trig) if trig > 0 else None,
            "qty": _s(_d(o.get("quantity") or o.get("origQty"))), "close_position": bool(o.get("closePosition")),
            "reduce_only": bool(o.get("reduceOnly")), "working_type": o.get("workingType"),
            "created_at": _ms(o["createTime"]).isoformat() if o.get("createTime") else None,
        })
    return sorted(out, key=lambda x: (x["symbol"] or "", x["kind"], x["id"]))


def snapshot(now: datetime, account: Any, position_risk: Any, orders: Any, algo_orders: Any, mkts: dict[str, dict[str, Any]],
             configs: dict[str, dict[str, Any]] | None = None) -> Snapshot:
    a = account or {}
    return Snapshot(
        synced_at=now,
        wallet_balance=_d(a.get("totalWalletBalance")),
        margin_balance=_d(a.get("totalMarginBalance")),
        available_balance=_d(a.get("availableBalance")),
        unrealized_pnl=_d(a.get("totalUnrealizedProfit")),
        positions=positions(position_risk, configs),
        open_orders=open_orders(orders, algo_orders),
        markets=mkts,
    )


def fill_row(t: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": t["symbol"], "trade_id": int(t["id"]), "order_id": int(t["orderId"]), "side": t["side"],
        "qty": _d(t["qty"]), "price": _d(t["price"]), "realized_pnl": _d(t.get("realizedPnl")),
        "commission": _d(t.get("commission")), "commission_asset": t.get("commissionAsset") or "USDT",
        "maker": bool(t.get("maker")), "traded_at": _ms(t["time"]),
    }


def income_row(i: dict[str, Any]) -> dict[str, Any]:
    return {
        "tran_id": int(i["tranId"]), "kind": str(i["incomeType"]), "symbol": i.get("symbol") or None,
        "amount": _d(i["income"]), "asset": i.get("asset") or "USDT", "info": str(i.get("info") or ""),
        "trade_id": int(i["tradeId"]) if str(i.get("tradeId") or "").isdigit() else None, "at": _ms(i["time"]),
    }


# --- risk state (high-water mark and UTC day open), kept across restarts ------

@dataclass(frozen=True)
class RiskState:
    day: date
    day_open: Decimal
    hwm: Decimal
    hwm_at: datetime
    start_equity: Decimal
    started_at: datetime


def mark_risk(prev: RiskState | None, equity: Decimal, now: datetime) -> RiskState:
    """Advance the high-water mark and roll the day open at 00:00 UTC (first mark of the day)."""
    today = now.astimezone(UTC).date()
    if prev is None:
        return RiskState(today, equity, equity, now, equity, now)
    day, day_open = (prev.day, prev.day_open) if prev.day == today else (today, equity)
    hwm, hwm_at = (equity, now) if equity > prev.hwm else (prev.hwm, prev.hwm_at)
    return RiskState(day, day_open, hwm, hwm_at, prev.start_equity, prev.started_at)
