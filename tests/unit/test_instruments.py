from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from trading.contracts.instruments import (
    COST_BASIS,
    CostBasisMethod,
    Instrument,
    InstrumentType,
)

EXP = datetime(2026, 12, 25, 8, tzinfo=UTC)


def test_spot_perp_dated_future_and_option_shapes():
    spot = Instrument(venue="binance", symbol="BTCUSDT", type="SPOT", base="BTC", quote="USDT")
    perp = Instrument(venue="binance", symbol="BTCUSDT", type="PERP_FUTURE", base="BTC", quote="USDT",
                      settle="USDT", contract_size="1")
    dated = Instrument(venue="deribit", symbol="BTC-25DEC26", type="DATED_FUTURE", base="BTC", quote="USD",
                       settle="BTC", contract_size="10", expiry=EXP)
    opt = Instrument(venue="deribit", symbol="BTC-25DEC26-100000-C", type="OPTION", base="BTC", quote="USD",
                     settle="BTC", contract_size="1", expiry=EXP, strike="100000", right="CALL")
    assert [i.enabled_in_build for i in (spot, perp, dated, opt)] == [True, True, False, False]


def test_average_cost_is_spot_only():
    assert [t for t, m in COST_BASIS.items() if m is CostBasisMethod.AVERAGE_COST] == [InstrumentType.SPOT]
    assert set(COST_BASIS) == set(InstrumentType)


@pytest.mark.parametrize("kwargs", [
    {"type": "SPOT", "settle": "USDT"},
    {"type": "PERP_FUTURE"},
    {"type": "PERP_FUTURE", "settle": "USDT", "contract_size": "1", "expiry": EXP},
    {"type": "DATED_FUTURE", "settle": "BTC", "contract_size": "10"},
    {"type": "OPTION", "settle": "BTC", "contract_size": "1", "expiry": EXP, "strike": "1"},
    {"type": "OPTION", "settle": "BTC", "contract_size": "1", "expiry": datetime(2026, 12, 25),
     "strike": "1", "right": "PUT"},
])
def test_mismatched_fields_are_refused(kwargs):
    with pytest.raises(ValidationError):
        Instrument(venue="x", symbol="X", base="BTC", quote="USD", **kwargs)
