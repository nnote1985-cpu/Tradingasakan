"""The standing futures risk rules (project memory 2026-11-17; futures-desk-plan.md §2).

Changed only by a reviewed code change, never from the web page or the environment.
"""

from __future__ import annotations

from decimal import Decimal

RISK_PER_TRADE = Decimal("0.01")   # a stop-out loses about 1% of equity
DAY_LOSS_CAP = Decimal("0.03")     # no new trades after equity falls 3% below the UTC day open
TOTAL_LOSS_CAP = Decimal("0.10")   # no new trades after equity falls 10% below its high-water mark
NEAR = Decimal("0.7")              # "near the cap" warning at 70% of a cap
SYMBOLS = ("BTCUSDT", "ETHUSDT")
