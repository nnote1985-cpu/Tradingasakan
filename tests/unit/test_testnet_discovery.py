"""P0: scripts/testnet_discovery.py stays read-only and Testnet-only."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("testnet_discovery", ROOT / "scripts/testnet_discovery.py")
assert spec and spec.loader
td = importlib.util.module_from_spec(spec)
sys.modules["testnet_discovery"] = td
spec.loader.exec_module(td)


def test_hmac_signature_matches_binance_docs_example():
    secret = "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j"
    payload = ("symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1"
               "&recvWindow=5000&timestamp=1499827319559")
    assert td.hmac_signature(secret, payload) == "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71"


def test_ed25519_signature_verifies():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    import base64
    sig = td.ed25519_signature(pem, "a=1")
    key.public_key().verify(base64.b64decode(sig), b"a=1")


@pytest.mark.parametrize("path", ["/api/v3/order", "/api/v3/order/test", "/api/v3/openOrders/cancel", "/sapi/v1/capital/withdraw/apply"])
def test_order_and_wallet_paths_are_refused(path):
    tn = td.Testnet("k", "s", get=lambda *a, **k: pytest.fail("network called"))
    with pytest.raises(td.Refused):
        tn.call(path)


def test_hosts_are_fixed_and_testnet_only():
    assert td.TESTNET_REST == "https://testnet.binance.vision"
    assert "testnet" in td.TESTNET_STREAM and "testnet" in td.TESTNET_WS_API
    assert all("/order" not in p for p in td.ALLOWED_PATHS)


def test_run_reports_without_secrets(monkeypatch):
    calls: list[tuple[str, dict]] = []

    def fake_get(url, headers=None):
        calls.append((url, headers or {}))
        if "/api/v3/time" in url:
            import time
            return 200, {"serverTime": int(time.time() * 1000)}, 5.0, {}
        if "exchangeInfo" in url:
            return 200, {"symbols": [{"status": "TRADING", "orderTypes": ["LIMIT", "MARKET"], "filters": [
                {"filterType": "LOT_SIZE", "stepSize": "0.00001000"}]}], "rateLimits": []}, 5.0, {}
        if "/api/v3/account/commission" in url:
            return 200, {"standardCommission": {"taker": "0.001"}}, 5.0, {}
        if "/api/v3/account" in url:
            return 200, {"accountType": "SPOT", "canTrade": True, "permissions": ["SPOT"],
                         "balances": [{"asset": "USDT", "free": "10000", "locked": "0"}]}, 5.0, {}
        if "openOrders" in url:
            return 200, [], 5.0, {}
        return 200, {}, 5.0, {}

    tn = td.Testnet("ABCDsecretkeymaterial", "topsecret", get=fake_get)
    r = td.run(tn, websockets_too=False, get=fake_get)
    text = repr(r)
    assert "topsecret" not in text and "ABCDsecretkeymaterial" not in text
    assert r["account"]["key"] == "ABCD…" and r["account"]["canTrade"] is True
    assert r["btcusdt"]["filters"]["LOT_SIZE"]["stepSize"] == "0.00001000"
    testnet_urls = [u for u, _ in calls if "binance.vision" in u and "data" not in u.split("//")[1].split(".")[0]]
    assert all(u.startswith("https://testnet.binance.vision/") for u in testnet_urls)
    signed = [u for u, h in calls if "X-MBX-APIKEY" in h]
    assert signed and all(u.startswith("https://testnet.binance.vision/") and "signature=" in u for u in signed)
    assert td.summary(r)
