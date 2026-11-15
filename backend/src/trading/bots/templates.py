"""Strategy templates a configured bot can run (docs/m4-bot-config-plan.md §3.1).

This is not the numbered recipe catalog (``trading.strategies.library.catalog``): a template here
has a strategy class that implements the M3 contract and can trade on Testnet. Each entry pins the
template id and version, the market and symbol, the timeframes it decides on, its parameters with
bounds, and the allocation it may be given.

Adding a template: append an entry. Changing what a template does: new version, never an edit.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from trading.contracts.strategy import Market, Strategy

MAX_ORDER_NOTIONAL = Decimal("2000")        # Testnet order cap (execution.limits): one rebalance is one order


@dataclass(frozen=True)
class TemplateParam:
    default: int | float
    min: int | float
    max: int | float
    label_th: str = ""

    def error(self, value: Any) -> str | None:
        """Why ``value`` is not allowed, or None."""
        if isinstance(value, bool) or not isinstance(value, int | float):
            return "ต้องเป็นตัวเลข"
        if isinstance(self.default, int) and not isinstance(value, int):
            return "ต้องเป็นจำนวนเต็ม"
        if not self.min <= value <= self.max:
            return f"ต้องอยู่ระหว่าง {self.min} ถึง {self.max}"
        return None


@dataclass(frozen=True)
class Template:
    template: str
    version: str
    name_th: str
    market: Market
    symbol: str
    timeframes: tuple[str, ...]
    params: Mapping[str, TemplateParam]
    min_allocation: Decimal
    max_allocation: Decimal
    summary_th: str
    factory: Callable[[Mapping[str, Any]], Strategy] = field(repr=False, compare=False)

    def resolve(self, params: Mapping[str, Any]) -> dict[str, Any]:
        """``params`` with defaults filled in; assumes they were checked."""
        return {k: params.get(k, p.default) for k, p in self.params.items()}

    def public(self) -> dict[str, Any]:
        return {"template": self.template, "version": self.version, "name_th": self.name_th, "market": self.market.value,
                "symbol": self.symbol, "timeframes": list(self.timeframes), "summary_th": self.summary_th,
                "params": {k: {"default": p.default, "min": p.min, "max": p.max, "label_th": p.label_th}
                           for k, p in self.params.items()},
                "allocation": {"asset": "USDT", "min": str(self.min_allocation), "max": str(self.max_allocation)}}


def _r10a_v2(_params: Mapping[str, Any]) -> Strategy:
    from trading.strategies.r10a_v2 import R10aV2

    return R10aV2()


TEMPLATES: dict[str, Template] = {t.template: t for t in (
    Template(
        template="tn-r10a-code-v2", version="tn-r10a-code-v2", name_th="R10a v2 (SMA100 ±2% + funding)",
        market=Market.SPOT, symbol="BTCUSDT", timeframes=("1d",), params={},
        min_allocation=Decimal("100"), max_allocation=MAX_ORDER_NOTIONAL,
        summary_th="ถือ BTC เมื่อราคา perp อยู่เหนือ SMA100 +2% ออกเมื่อต่ำกว่า −2%; funding 7 วันร้อนเกิน 0.09% ถือครึ่งเดียว "
                   "ตัดสินใจวันละครั้งจากแท่งปิดรายวัน กฎเท่ากับ v1.1 ทุกอย่าง จึงไม่มีค่าให้ปรับ",
        factory=_r10a_v2),
)}


def get(template: str) -> Template | None:
    return TEMPLATES.get(template)
