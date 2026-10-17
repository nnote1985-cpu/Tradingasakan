"""Process configuration (BUILDPLAN v4.2 §21.6), validated at startup.

Only references to secrets live here; values come from the environment or the
repository root .env file. Process environment variables take precedence.

LIVE is refused outright in this build: there is no real order adapter, and
setting APP_ENV=production or LIVE_ENABLED=true is not a live approval. Live
approval is a separate, recorded decision by the owner (§23) in a later build
that ships an adapter and flips LIVE_SUPPORTED_IN_BUILD after review.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from trading.contracts.enums import TradingMode

# Changed only by a reviewed code change that also adds a real order adapter.
LIVE_SUPPORTED_IN_BUILD = False
ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, env_file_encoding="utf-8", extra="ignore", frozen=True)

    app_env: Literal["development", "test", "staging", "production"] = "development"
    trading_mode: TradingMode = TradingMode.SHADOW
    live_enabled: bool = False
    # TESTNET orders also need this flag (testnet-development-plan.md §2.1); without it TESTNET loads no signing key.
    testnet_execution_enabled: bool = False
    database_url: str | None = Field(default=None, repr=False)        # server-only
    supabase_url: str | None = None
    supabase_publishable_key: str | None = None
    telegram_credential_ref: str | None = Field(default=None, repr=False)
    allowed_origins: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _live_requires_explicit_enable(self) -> Settings:
        if not LIVE_SUPPORTED_IN_BUILD and (self.trading_mode is TradingMode.LIVE or self.live_enabled):
            raise ValueError("LIVE is not available in this build (no order adapter); env settings cannot enable it")
        if self.trading_mode is TradingMode.LIVE and not self.live_enabled:
            raise ValueError("TRADING_MODE=LIVE requires LIVE_ENABLED=true")
        if self.live_enabled and self.app_env != "production":
            raise ValueError("LIVE_ENABLED is only allowed with APP_ENV=production")
        if self.testnet_execution_enabled and self.trading_mode is not TradingMode.TESTNET:
            raise ValueError("TESTNET_EXECUTION_ENABLED=true requires TRADING_MODE=TESTNET")
        return self


def load_settings() -> Settings:
    return Settings(_env_file=ENV_FILE)
