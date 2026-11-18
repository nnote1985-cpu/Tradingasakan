"""Binance USDⓈ-M Futures Demo REST client (futures-desk-plan.md §3).

Every URL is built from :data:`Venue.FUTURES_DEMO` and passes the endpoint guards,
so a signed call cannot reach the Mainnet ``fapi.binance.com``. Stage 1 only reads:
there is no method here that places, changes or cancels an order.

Keys come from the Demo Trading site (demo.binance.com), not testnet.binance.vision,
and live only in the Host environment:

    FUTURES_DEMO_API_KEY + FUTURES_DEMO_API_SECRET (HMAC) or FUTURES_DEMO_PRIVATE_KEY_PATH (Ed25519)
"""

from __future__ import annotations

import os
import time
import urllib.parse
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from trading.runtime import endpoints
from trading.runtime.credentials import CredentialError, TestnetCredentials, mainnet_vars

KEY_ENV = "FUTURES_DEMO_API_KEY"
SECRET_ENV = "FUTURES_DEMO_API_SECRET"
PEM_ENV = "FUTURES_DEMO_PRIVATE_KEY_PATH"

Send = Callable[[str, str, dict[str, str]], tuple[int, Any, dict[str, str]]]


class FuturesError(RuntimeError):
    """Binance answered with an error; ``code`` is its error code when it sent one."""

    def __init__(self, path: str, status: int, data: Any) -> None:
        self.status = status
        self.code = data.get("code") if isinstance(data, dict) else None
        msg = data.get("msg") if isinstance(data, dict) else None
        super().__init__(f"{path}: HTTP {status}{f' code {self.code}' if self.code is not None else ''}{f' {msg}' if msg else ''}")


def load_demo_credentials(env: Mapping[str, str] | None = None) -> TestnetCredentials:
    """The Demo Trading key. Refused when any Mainnet key variable is set on the Host."""
    env = os.environ if env is None else env
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


class FuturesDemoRest:
    """One request per call, never retried here. After 429/418 it refuses calls until Retry-After has passed."""

    def __init__(self, creds: TestnetCredentials | None, send: Send = endpoints.request_json,
                 wall_ms: Callable[[], float] | None = None) -> None:
        self.creds, self._send = creds, send
        self._wall_ms = wall_ms or (lambda: time.time() * 1000)
        self.offset_ms = 0.0
        self.blocked_until_ms = 0.0

    def _request(self, method: str, path: str, params: dict[str, Any], *, signed: bool) -> Any:
        if self._wall_ms() < self.blocked_until_ms:
            raise endpoints.RateLimited(f"Binance asked us to wait {int((self.blocked_until_ms - self._wall_ms()) / 1000)} s more")
        if signed:
            if self.creds is None:
                raise CredentialError("no Futures Demo credentials loaded")
            q = urllib.parse.urlencode(params | {"recvWindow": 5000, "timestamp": int(self._wall_ms() + self.offset_ms)})
            q += "&signature=" + urllib.parse.quote(self.creds.sign(q), safe="")
            url = endpoints.rest_url(endpoints.Venue.FUTURES_DEMO, path, q)
            endpoints.check_signed(url)
            headers = {"X-MBX-APIKEY": self.creds.api_key}
        else:
            url = endpoints.rest_url(endpoints.Venue.FUTURES_DEMO, path, urllib.parse.urlencode(params))
            endpoints.check_public(url, method)
            headers = {}
        status, data, hdrs = self._send(method, url, headers)
        if status in (418, 429):
            wait = next((v for k, v in hdrs.items() if k.lower() == "retry-after"), "60")
            self.blocked_until_ms = self._wall_ms() + 1000 * (float(wait) if str(wait).replace(".", "", 1).isdigit() else 60.0)
            raise endpoints.RateLimited(f"Binance answered HTTP {status}; waiting {wait} s")
        if status != 200:
            raise FuturesError(path, status, data)
        return data

    def get(self, path: str, params: dict[str, Any] | None = None, *, signed: bool = True) -> Any:
        return self._request("GET", path, params or {}, signed=signed)

    # --- reads used by the sync (stage 1) ------------------------------------

    def sync_clock(self) -> float:
        before = self._wall_ms()
        data = self.get("/fapi/v1/time", signed=False)
        after = self._wall_ms()
        self.offset_ms = float(data["serverTime"]) - (before + after) / 2
        return self.offset_ms

    def exchange_info(self) -> Any:
        return self.get("/fapi/v1/exchangeInfo", signed=False)

    def premium_index(self) -> Any:
        return self.get("/fapi/v1/premiumIndex", signed=False)

    def account(self) -> Any:
        return self.get("/fapi/v3/account")

    def position_risk(self) -> Any:
        return self.get("/fapi/v3/positionRisk")

    def open_orders(self) -> Any:
        return self.get("/fapi/v1/openOrders")

    def open_algo_orders(self) -> Any:
        """Conditional orders (STOP_MARKET, TAKE_PROFIT_MARKET, ...) live in the Algo service since 2025-12."""
        return self.get("/fapi/v1/openAlgoOrders")

    def user_trades(self, symbol: str, from_id: int | None = None, start_ms: int | None = None) -> Any:
        params: dict[str, Any] = {"symbol": symbol, "limit": 1000}
        if from_id is not None:
            params["fromId"] = from_id
        elif start_ms is not None:
            params["startTime"] = start_ms
        return self.get("/fapi/v1/userTrades", params)

    def income(self, start_ms: int) -> Any:
        return self.get("/fapi/v1/income", {"startTime": start_ms, "limit": 1000})

    def position_mode(self) -> Any:
        return self.get("/fapi/v1/positionSide/dual")
