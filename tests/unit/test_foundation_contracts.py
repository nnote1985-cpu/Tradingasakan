from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError
from trading.config import Settings
from trading.contracts.enums import TradingMode
from trading.contracts.money import DecimalStr
from trading.contracts.versions import StrategyVersion


def test_defaults_are_shadow_and_live_disabled():
    s = Settings()
    assert s.trading_mode is TradingMode.SHADOW and not s.live_enabled


def test_live_mode_is_refused_without_explicit_enable():
    with pytest.raises(ValidationError):
        Settings(trading_mode="LIVE")
    with pytest.raises(ValidationError):
        Settings(live_enabled=True, app_env="development")


def test_secrets_are_not_in_repr():
    assert "postgres://secret" not in repr(Settings(database_url="postgres://secret"))


class _Amount(BaseModel):
    qty: DecimalStr


def test_amounts_are_decimal_strings_and_reject_floats():
    a = _Amount(qty="0.000123")
    assert a.qty == Decimal("0.000123")
    assert a.model_dump(mode="json") == {"qty": "0.000123"}
    with pytest.raises(ValidationError):
        _Amount(qty=0.1)


def test_strategy_version_label_and_commit_are_validated():
    v = StrategyVersion(label="pathA-v1", code_commit="42920ba", frozen_ref="frozen/pathA-v1")
    assert v.label == "pathA-v1"
    with pytest.raises(ValidationError):
        StrategyVersion(label="bad label", code_commit="42920ba")
