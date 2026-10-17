"""P0 discovery for Binance Spot Test Network (docs/testnet-development-plan.md §10, §13).

Run this on the Host that will run the workers (Note's PC for P1–P4, a VPS
before the P5 soak). It answers, from that Host, the P0 questions the plan
says must not be guessed:

- Can the Host reach the Spot Testnet REST API, its market streams and its
  WebSocket API? What is the clock offset to the Testnet server?
- What does Testnet exchangeInfo say about BTCUSDT (status, order types,
  filters, permissions), and what rate limits does it publish?
- Can the Host reach Mainnet *public* endpoints for the signal plane (§3.3)?
  Only unauthenticated ping/time calls; nothing is signed for Mainnet.
- With Testnet keys set: does the key authenticate on the Testnet, can the
  account trade Spot, what are its balances and commission rates?

Read-only: the script never calls an order, cancel or wallet endpoint, and
every host is a constant here (no base URL from the environment). Secrets
are never written to the report; the API key is shown only as its first four
characters.

    uv run python scripts/testnet_discovery.py                  # public checks only
    uv run python scripts/testnet_discovery.py --out p0.json    # also writes the report

Keys (Testnet only, from https://testnet.binance.vision):
    BINANCE_TESTNET_API_KEY                 always
    BINANCE_TESTNET_API_SECRET              for an HMAC key
    BINANCE_TESTNET_PRIVATE_KEY_PATH        for an Ed25519 key (PEM file)
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import os
import platform
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from typing import Any

TESTNET_REST = "https://testnet.binance.vision"
TESTNET_STREAM = "wss://stream.testnet.binance.vision/ws/btcusdt@bookTicker"
TESTNET_WS_API = "wss://ws-api.testnet.binance.vision/ws-api/v3"
MAINNET_PUBLIC = {
    "spot_api": "https://api.binance.com/api/v3/ping",
    "spot_data_api": "https://data-api.binance.vision/api/v3/ping",
    "usdm_futures": "https://fapi.binance.com/fapi/v1/ping",
    "archive": "https://data.binance.vision/?prefix=data/spot/daily/klines/BTCUSDT/1d/",
}
# The only Testnet REST paths this script may call. Order, cancel and wallet routes are absent on purpose.
ALLOWED_PATHS = frozenset({"/api/v3/ping", "/api/v3/time", "/api/v3/exchangeInfo", "/api/v3/account",
                           "/api/v3/account/commission", "/api/v3/openOrders"})
SIGNED_PATHS = frozenset({"/api/v3/account", "/api/v3/account/commission", "/api/v3/openOrders"})
TIMEOUT = 10


class Refused(RuntimeError):
    pass


def mask_key(key: str | None) -> str | None:
    return None if not key else key[:4] + "…"


def hmac_signature(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def ed25519_signature(pem: bytes, payload: str) -> str:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    key = load_pem_private_key(pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("BINANCE_TESTNET_PRIVATE_KEY_PATH is not an Ed25519 private key")
    return base64.b64encode(key.sign(payload.encode())).decode()


def _get(url: str, headers: dict[str, str] | None = None) -> tuple[int, Any, float, dict[str, str]]:
    req = urllib.request.Request(url, headers={"User-Agent": "tradingasakan-p0-discovery", **(headers or {})})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read()
            status, hdrs = r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        body, status, hdrs = e.read(), e.code, dict(e.headers or {})
    rtt = (time.monotonic() - t0) * 1000
    try:
        data: Any = json.loads(body) if body else None
    except ValueError:
        data = body[:200].decode(errors="replace")
    return status, data, rtt, hdrs


class Testnet:
    def __init__(self, api_key: str | None = None, secret: str | None = None, pem: bytes | None = None,
                 get=_get) -> None:  # type: ignore[no-untyped-def]
        self.api_key, self.secret, self.pem, self._get = api_key, secret, pem, get
        self.offset_ms = 0

    @property
    def has_key(self) -> bool:
        return bool(self.api_key and (self.secret or self.pem))

    def call(self, path: str, params: dict[str, Any] | None = None) -> tuple[int, Any, float, dict[str, str]]:
        if path not in ALLOWED_PATHS:
            raise Refused(f"{path} is not a read-only discovery path")
        q = dict(params or {})
        headers: dict[str, str] = {}
        if path in SIGNED_PATHS:
            if not self.has_key:
                raise Refused("signed call without Testnet keys")
            q |= {"recvWindow": 5000, "timestamp": int(time.time() * 1000) + self.offset_ms}
            payload = urllib.parse.urlencode(q)
            sig = hmac_signature(self.secret, payload) if self.secret else ed25519_signature(self.pem or b"", payload)
            q["signature"] = sig
            headers["X-MBX-APIKEY"] = self.api_key or ""
        url = TESTNET_REST + path + ("?" + urllib.parse.urlencode(q) if q else "")
        return self._get(url, headers)


def _filters(symbol_info: dict[str, Any]) -> dict[str, Any]:
    return {f["filterType"]: {k: v for k, v in f.items() if k != "filterType"} for f in symbol_info.get("filters", [])}


def rest_checks(tn: Testnet, report: dict[str, Any]) -> None:
    status, _, rtt, _ = tn.call("/api/v3/ping")
    report["testnet_rest"] = {"reachable": status == 200, "status": status, "rtt_ms": round(rtt, 1)}
    if status != 200:
        return
    before = time.time() * 1000
    status, data, rtt, _ = tn.call("/api/v3/time")
    after = time.time() * 1000
    if status == 200:
        server = data["serverTime"]
        offset = server - (before + after) / 2
        tn.offset_ms = int(offset)
        report["clock"] = {"server_time": datetime.fromtimestamp(server / 1000, UTC).isoformat(),
                           "offset_ms": round(offset, 1), "rtt_ms": round(rtt, 1),
                           "ok": abs(offset) < 1000}
    status, data, _, hdrs = tn.call("/api/v3/exchangeInfo", {"symbol": "BTCUSDT"})
    if status == 200 and data.get("symbols"):
        s = data["symbols"][0]
        report["btcusdt"] = {
            "status": s.get("status"), "orderTypes": s.get("orderTypes"),
            "permissionSets": s.get("permissionSets"), "isSpotTradingAllowed": s.get("isSpotTradingAllowed"),
            "ocoAllowed": s.get("ocoAllowed"), "otoAllowed": s.get("otoAllowed"),
            "cancelReplaceAllowed": s.get("cancelReplaceAllowed"), "filters": _filters(s),
        }
        report["rate_limits"] = data.get("rateLimits")
        report["used_weight_header"] = {k: v for k, v in hdrs.items() if k.lower().startswith("x-mbx-used-weight")}
    else:
        report["btcusdt"] = {"error": status, "body": data}


def account_checks(tn: Testnet, report: dict[str, Any]) -> None:
    if not tn.has_key:
        report["account"] = {"checked": False, "why": "no Testnet keys in the environment"}
        return
    status, data, _, _ = tn.call("/api/v3/account", {"omitZeroBalances": "true"})
    if status != 200:
        report["account"] = {"checked": True, "authenticated": False, "status": status, "error": data}
        return
    report["account"] = {
        "checked": True, "authenticated": True, "key": mask_key(tn.api_key),
        "key_type": "HMAC" if tn.secret else "Ed25519",
        "accountType": data.get("accountType"), "canTrade": data.get("canTrade"), "permissions": data.get("permissions"),
        "balances": {b["asset"]: {"free": b["free"], "locked": b["locked"]} for b in data.get("balances", [])},
    }
    status, data, _, _ = tn.call("/api/v3/account/commission", {"symbol": "BTCUSDT"})
    report["commission"] = data if status == 200 else {"error": status, "body": data}
    status, data, _, _ = tn.call("/api/v3/openOrders", {"symbol": "BTCUSDT"})
    report["open_orders_btcusdt"] = len(data) if status == 200 and isinstance(data, list) else {"error": status}


async def _ws_first(url: str, send: dict[str, Any] | None = None) -> dict[str, Any]:
    import websockets

    t0 = time.monotonic()
    try:
        async with websockets.connect(url, open_timeout=TIMEOUT, close_timeout=2) as ws:
            connected = (time.monotonic() - t0) * 1000
            if send is not None:
                await ws.send(json.dumps(send))
            msg = json.loads(await asyncio.wait_for(ws.recv(), TIMEOUT))
            return {"reachable": True, "connect_ms": round(connected, 1),
                    "first_message_ms": round((time.monotonic() - t0) * 1000, 1),
                    "sample": {k: msg[k] for k in list(msg)[:6]} if isinstance(msg, dict) else str(msg)[:200]}
    except Exception as e:  # noqa: BLE001 - every failure is a finding here
        return {"reachable": False, "error": f"{type(e).__name__}: {e}"[:300]}


def ws_checks(report: dict[str, Any]) -> None:
    report["testnet_market_stream"] = asyncio.run(_ws_first(TESTNET_STREAM))
    report["testnet_ws_api"] = asyncio.run(_ws_first(TESTNET_WS_API, {"id": "p0", "method": "ping"}))


def mainnet_public_checks(report: dict[str, Any], get=_get) -> None:  # type: ignore[no-untyped-def]
    out = {}
    for name, url in MAINNET_PUBLIC.items():
        try:
            status, _, rtt, _ = get(url)
            out[name] = {"status": status, "reachable": status == 200, "rtt_ms": round(rtt, 1)}
        except Exception as e:  # noqa: BLE001
            out[name] = {"reachable": False, "error": f"{type(e).__name__}: {e}"[:200]}
    report["mainnet_public_readonly"] = out


def run(tn: Testnet, *, websockets_too: bool = True, get=_get) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    report: dict[str, Any] = {"schema": "testnet_discovery.v1", "at": datetime.now(UTC).isoformat(),
                              "environment": "BINANCE_SPOT_TEST_NETWORK", "host": platform.node(),
                              "platform": platform.platform()}
    try:
        rest_checks(tn, report)
    except urllib.error.URLError as e:
        report["testnet_rest"] = {"reachable": False, "error": str(e.reason)[:200]}
    if report.get("testnet_rest", {}).get("reachable"):
        account_checks(tn, report)
    else:
        report["account"] = {"checked": False, "why": "Testnet REST unreachable from this Host"}
    if websockets_too:
        ws_checks(report)
    mainnet_public_checks(report, get)
    return report


def summary(r: dict[str, Any]) -> list[str]:
    def ok(x: Any) -> str:
        return "OK " if x else "NO "
    lines = [f"{ok(r.get('testnet_rest', {}).get('reachable'))} Testnet REST {r.get('testnet_rest')}"]
    if "clock" in r:
        lines.append(f"{ok(r['clock']['ok'])} clock offset {r['clock']['offset_ms']} ms")
    b = r.get("btcusdt", {})
    if "status" in b:
        lines.append(f"{ok(b['status'] == 'TRADING')} BTCUSDT {b['status']}, order types {b.get('orderTypes')}")
    for k in ("testnet_market_stream", "testnet_ws_api"):
        if k in r:
            lines.append(f"{ok(r[k].get('reachable'))} {k} {r[k].get('error') or ''}")
    a = r.get("account", {})
    lines.append(f"{ok(a.get('authenticated'))} account " + (json.dumps({k: a.get(k) for k in ('canTrade', 'permissions', 'balances')})
                                                             if a.get('authenticated') else str(a.get('why') or a.get('error'))))
    for name, v in r.get("mainnet_public_readonly", {}).items():
        lines.append(f"{ok(v.get('reachable'))} mainnet public {name} {v.get('status', v.get('error'))}")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", help="write the JSON report here (contains no secrets)")
    ap.add_argument("--no-websockets", action="store_true")
    args = ap.parse_args()
    pem_path = os.environ.get("BINANCE_TESTNET_PRIVATE_KEY_PATH")
    tn = Testnet(os.environ.get("BINANCE_TESTNET_API_KEY"), os.environ.get("BINANCE_TESTNET_API_SECRET"),
                 open(pem_path, "rb").read() if pem_path else None)
    report = run(tn, websockets_too=not args.no_websockets)
    print("\n".join(summary(report)))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"report written to {args.out}")
    return 0 if report.get("testnet_rest", {}).get("reachable") else 1


if __name__ == "__main__":
    sys.exit(main())
