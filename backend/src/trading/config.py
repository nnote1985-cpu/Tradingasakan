"""Process configuration (BUILDPLAN v4.2 §21.6), validated at startup.

Only references to secrets live here; values come from the environment. LIVE
is refused unless explicitly enabled, and Phase 1 never enables it by default.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from trading.contracts.enums import TradingMode


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", frozen=True)

    app_env: Literal["development", "test", "staging", "production"] = "development"
    trading_mode: TradingMode = TradingMode.SHADOW
    live_enabled: bool = False
    database_url: str | None = Field(default=None, repr=False)        # server-only
    supabase_url: str | None = None
    supabase_publishable_key: str | None = None
    telegram_credential_ref: str | None = Field(default=None, repr=False)
    allowed_origins: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _live_requires_explicit_enable(self) -> Settings:
        if self.trading_mode is TradingMode.LIVE and not self.live_enabled:
            raise ValueError("TRADING_MODE=LIVE requires LIVE_ENABLED=true")
        if self.live_enabled and self.app_env != "production":
            raise ValueError("LIVE_ENABLED is only allowed with APP_ENV=production")
        return self


def load_settings() -> Settings:
    return Settings()
