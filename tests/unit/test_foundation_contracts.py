from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

import trading.config as config
from trading.config import Settings
from trading.contracts.enums import TradingMode
from trading.contracts.money import DecimalStr
from trading.contracts.versions import StrategyVersion


def test_load_settings_reads_dotenv_independent_of_working_directory(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text('SUPABASE_URL=https://example.supabase.co\nDATABASE_URL=postgresql://example\n'
                        'ALLOWED_ORIGINS=["http://localhost:5173"]\n', encoding="utf-8")
    monkeypatch.setattr(config, "ENV_FILE", env_file)
    for name in ("SUPABASE_URL", "DATABASE_URL", "ALLOWED_ORIGINS"):
        monkeypatch.delenv(name, raising=False)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    s = config.load_settings()
    assert s.supabase_url == "https://example.supabase.co"
    assert s.database_url == "postgresql://example"
    assert s.allowed_origins == ("http://localhost:5173",)


def test_load_settings_environment_overrides_dotenv(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("SUPABASE_URL=https://file.supabase.co\n", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_FILE", env_file)
    monkeypatch.setenv("SUPABASE_URL", "https://process.supabase.co")
    assert config.load_settings().supabase_url == "https://process.supabase.co"


def test_load_settings_missing_dotenv_is_optional(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / "missing.env")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    assert config.load_settings().supabase_url is None


def test_dotenv_path_is_repository_root():
    assert config.ENV_FILE == Path(__file__).resolve().parents[2] / ".env"


def test_defaults_are_shadow_and_live_disabled():
    s = Settings()
    assert s.trading_mode is TradingMode.SHADOW and not s.live_enabled


def test_live_mode_is_refused_without_explicit_enable():
    with pytest.raises(ValidationError):
        Settings(trading_mode="LIVE")
    with pytest.raises(ValidationError):
        Settings(live_enabled=True, app_env="development")


def test_production_env_and_flags_cannot_enable_live_in_this_build():
    for kwargs in (
        {"app_env": "production", "live_enabled": True},
        {"app_env": "production", "live_enabled": True, "trading_mode": "LIVE"},
        {"app_env": "production", "trading_mode": "LIVE"},
    ):
        with pytest.raises(ValidationError, match="not available in this build"):
            Settings(**kwargs)
    assert Settings(app_env="production").trading_mode is TradingMode.SHADOW


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
