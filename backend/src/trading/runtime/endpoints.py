"""Endpoint guards (testnet-development-plan.md §3.2).

Hosts are constants: callers pick a ``Venue``, never a base URL. Signed calls
may only go to the Spot Testnet host, over https, with no port or user info.
Public reads go through a separate check that refuses API keys and
signatures, so the Mainnet signal client has no signing path at all.
Redirects are refused rather than followed, so a 30x cannot move a signed
request to another host.
"""

from __future__ import annotations

import urllib.parse
import urllib.request
from enum import StrEnum
from typing import Any, NoReturn


class Venue(StrEnum):
    SPOT_TESTNET = "SPOT_TESTNET"


REST_BASE: dict[Venue, str] = {Venue.SPOT_TESTNET: "https://testnet.binance.vision"}
SIGNED_HOSTS = frozenset({"testnet.binance.vision"})
# Read-only, unauthenticated hosts for the signal plane (§3.3) and the Testnet's own public routes.
PUBLIC_HOSTS = frozenset({"testnet.binance.vision", "api.binance.com", "data-api.binance.vision", "fapi.binance.com",
                          "data.binance.vision"})
_SIGNING_PARAMS = frozenset({"signature", "timestamp", "recvwindow"})


class EndpointRefused(RuntimeError):
    pass


def _parse(url: str) -> urllib.parse.SplitResult:
    u = urllib.parse.urlsplit(url)
    if u.scheme != "https":
        raise EndpointRefused(f"only https is allowed, got {u.scheme or 'no scheme'}")
    if u.username or u.password or u.port is not None:
        raise EndpointRefused("user info and explicit ports are not allowed")
    return u


def rest_url(venue: Venue, path: str, query: str = "") -> str:
    if not path.startswith("/api/"):
        raise EndpointRefused(f"{path} is not a REST API path")
    return REST_BASE[venue] + path + ("?" + query if query else "")


def check_signed(url: str) -> None:
    """Raise unless ``url`` is a Spot Testnet URL. Mainnet hosts are refused whatever the mode or keys."""
    host = _parse(url).hostname or ""
    if host not in SIGNED_HOSTS:
        raise EndpointRefused(f"signed or mutating calls may only go to the Spot Testnet, not {host}")


def check_public(url: str, method: str, headers: dict[str, str] | None = None) -> None:
    """Raise unless ``url`` is an allowed read-only GET that carries no key or signature."""
    if method.upper() != "GET":
        raise EndpointRefused(f"public client is read-only; {method} refused")
    u = _parse(url)
    if (u.hostname or "") not in PUBLIC_HOSTS:
        raise EndpointRefused(f"{u.hostname} is not an allowed public data host")
    if any(k.lower() == "x-mbx-apikey" for k in (headers or {})):
        raise EndpointRefused("public client must not send an API key")
    if {k.lower() for k in urllib.parse.parse_qs(u.query)} & _SIGNING_PARAMS:
        raise EndpointRefused("public client must not send signed parameters")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: urllib.request.Request, fp: Any, code: int, msg: str, headers: Any,
                         newurl: str) -> NoReturn:
        raise EndpointRefused(f"redirect {code} to {urllib.parse.urlsplit(newurl).hostname} refused")


def opener() -> urllib.request.OpenerDirector:
    """An opener that never follows redirects."""
    return urllib.request.build_opener(_NoRedirect)
