"""Forward paper books for R10a and R11 (docs/band-books-plan.md).

The books are pathA-v1.2's ``PathABook`` and futures-r7's ``TrendPerpBook`` unchanged; only the
rules version differs, so a state file can only be loaded by the book that wrote it. The band rule
itself is ``trading.strategies.sma_band.band_state``, applied by the scripts that make decisions.
"""

from __future__ import annotations

from trading.shadow.rebalance_book import PathABook
from trading.shadow.trend_perp_book import TrendPerpBook

R10A_RULES_VERSION = "pathA-r10a"
R11_RULES_VERSION = "futures-r11"


class BandSpotBook(PathABook):
    rules_version = R10A_RULES_VERSION


class BandPerpBook(TrendPerpBook):
    rules_version = R11_RULES_VERSION
