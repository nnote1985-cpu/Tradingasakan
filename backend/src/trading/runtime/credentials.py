"""Testnet signing credentials (testnet-development-plan.md §2.2, §3.2).

Keys live only in environment variables on the Host. They are loaded only in
TESTNET mode with TESTNET_EXECUTION_ENABLED=true, and never when a Mainnet
key variable is present, so a misplaced Mainnet key stops the worker instead
of sitting next to the Testnet one. The object never prints its secret, and
the API key shows only its first four characters.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from trading.config import Settings
from trading.contracts.enums import TradingMode

KEY_ENV = "BINANCE_TESTNET_API_KEY"
SECRET_ENV = "BINANCE_TESTNET_API_SECRET"
PEM_ENV = "BINANCE_TESTNET_PRIVATE_KEY_PATH"
# Variables that would hold Mainnet credentials. None may exist in this deployment (§3.2.3).
MAINNET_ENV = ("BINANCE_API_KEY", "BINANCE_API_SECRET", "BINANCE_SECRET_KEY", "BINANCE_SECRET", "BINANCE_PRIVATE_KEY_PATH")


class CredentialError(RuntimeError):
    pass


def mask(key: str) -> str:
    return key[:4] + "…" if key else ""


@dataclass(frozen=True)
class TestnetCredentials:
    __test__ = False
    api_key: str = field(repr=False)
    secret: str | None = field(default=None, repr=False)
    pem: bytes | None = field(default=None, repr=False)

    @property
    def key_type(self) -> str:
        return "HMAC" if self.secret else "Ed25519"

    def __repr__(self) -> str:
        return f"TestnetCredentials(key={mask(self.api_key)}, type={self.key_type})"

    def sign(self, payload: str) -> str:
        if self.secret:
            return hmac.new(self.secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import load_pem_private_key

        key = load_pem_private_key(self.pem or b"", password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise CredentialError(f"{PEM_ENV} is not an Ed25519 private key")
        return base64.b64encode(key.sign(payload.encode())).decode()


def mainnet_vars(env: Mapping[str, str]) -> list[str]:
    return [n for n in MAINNET_ENV if env.get(n, "").strip()]


def load_testnet_credentials(settings: Settings, env: Mapping[str, str] | None = None) -> TestnetCredentials:
    env = os.environ if env is None else env
    if settings.trading_mode is not TradingMode.TESTNET or not settings.testnet_execution_enabled:
        raise CredentialError("signing keys load only with TRADING_MODE=TESTNET and TESTNET_EXECUTION_ENABLED=true")
    if found := mainnet_vars(env):
        raise CredentialError(f"Mainnet key variables are set ({', '.join(found)}); remove them from this Host")
    key = env.get(KEY_ENV, "").strip()
    secret = env.get(SECRET_ENV, "").strip() or None
    pem_path = env.get(PEM_ENV, "").strip() or None
    if not key:
        raise CredentialError(f"{KEY_ENV} is not set")
    if bool(secret) == bool(pem_path):
        raise CredentialError(f"set exactly one of {SECRET_ENV} (HMAC) or {PEM_ENV} (Ed25519)")
    pem = None
    if pem_path:
        try:
            pem = Path(pem_path).read_bytes()
        except OSError as e:
            raise CredentialError(f"cannot read {PEM_ENV}: {e.strerror}") from None
    return TestnetCredentials(api_key=key, secret=secret, pem=pem)
