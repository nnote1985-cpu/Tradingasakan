"""Named risk profiles.

``SPOT_V1`` was chosen by Note on 2026-10-15 for spot books (the futures 2%/8%
profile would halt a book fully in BTC spot within weeks): alert at a 5% loss
in one UTC day or a 15% drawdown, and HALT (sell to cash, wait for Note to
resume) at a 25% drawdown from the high-water mark. RUIN always halts. No
leverage: gross notional and exchange leverage are 1x.
"""

from __future__ import annotations

from decimal import Decimal

from trading.risk.sizing import RiskProfile

FUTURES_DEFAULT = RiskProfile()

SPOT_V1 = RiskProfile(
    daily_loss_trigger_fraction=Decimal("0.05"),
    daily_loss_action="ALERT",
    drawdown_alert_fraction=Decimal("0.15"),
    portfolio_drawdown_halt_fraction=Decimal("0.25"),
    max_gross_notional_to_equity=Decimal("1"),
    requested_exchange_leverage=Decimal("1"),
    max_margin_utilization_fraction=Decimal("1"),
)

# Same levels on Testnet, but Guardian checks them on every fresh quote with the book marked at the bid,
# not once a day on the close (testnet-development-plan.md §8.5, docs/research/intraday-risk-plan.md).
SPOT_TN_V1 = SPOT_V1

PROFILES = {"futures.default": FUTURES_DEFAULT, "spot.v1": SPOT_V1, "spot.tn.v1": SPOT_TN_V1}
