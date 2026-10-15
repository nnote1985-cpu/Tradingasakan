"""Decimal amounts travel as strings (§20.1 NUMERIC, §21.3 amounts as strings)."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from pydantic import BeforeValidator, PlainSerializer


def _to_decimal(v: object) -> Decimal:
    if isinstance(v, float):
        raise ValueError("floats are not accepted for amounts; pass a string or Decimal")
    d = Decimal(str(v))
    if not d.is_finite():
        raise ValueError("amount must be finite")
    return d


DecimalStr = Annotated[Decimal, BeforeValidator(_to_decimal), PlainSerializer(lambda d: format(d, "f"), return_type=str)]
