"""P1 runtime: modes, endpoint guards, credentials, clock, alerts, dead-man and the worker (testnet-development-plan.md §10 P1)."""

from __future__ import annotations

import base64
import http.server
import json
import threading
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from trading.config import Settings
from trading.contracts.enums import TradingMode
from trading.execution.safety import AccountSafety, SafetyState
from trading.runtime import endpoints
from trading.runtime.alerts import AlertQueue, Severity, TelegramSink, mask
from trading.runtime.clock import ClockStatus, reading
from trading.runtime.credentials import CredentialError, TestnetCredentials, load_testnet_credentials
from trading.runtime.deadman import DeadMan
from trading.runtime.worker import LeaseLost, TestnetRest, Worker, WorkerConfig, WorkerRefused

T0 = datetime(2026, 10, 17, 3, tzinfo=UTC)
TESTNET = Settings(trading_mode="TESTNET", testnet_execution_enabled=True)
SHADOW = Settings()


# --- modes ------------------------------------------------------------------------------------------------

def test_execution_flag_needs_testnet_mode():
    with pytest.raises(ValidationError, match="requires TRADING_MODE=TESTNET"):
        Settings(testnet_execution_enabled=True)
    assert TESTNET.trading_mode is TradingMode.TESTNET


def test_live_still_refused_with_testnet_flags():
    with pytest.raises(ValidationError, match="not available in this build"):
        Settings(trading_mode="LIVE", live_enabled=True, app_env="production", testnet_execution_enabled=True)


# --- endpoint guards (RT20) ----------------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "https://api.binance.com/api/v3/order",
    "https://fapi.binance.com/fapi/v1/order",
    "http://testnet.binance.vision/api/v3/order",
    "https://testnet.binance.vision:8443/api/v3/order",
    "https://user@testnet.binance.vision/api/v3/order",
    "https://testnet.binance.vision.evil.com/api/v3/order",
])
def test_signed_calls_only_reach_the_spot_testnet(url):
    with pytest.raises(endpoints.EndpointRefused):
        endpoints.check_signed(url)


def test_signed_testnet_url_passes():
    endpoints.check_signed(endpoints.rest_url(endpoints.Venue.SPOT_TESTNET, "/api/v3/order", "a=1"))


def test_rest_url_takes_a_venue_not_a_host():
    with pytest.raises(endpoints.EndpointRefused):
        endpoints.rest_url(endpoints.Venue.SPOT_TESTNET, "//api.binance.com/api/v3/order")
    assert set(endpoints.REST_BASE.values()) == {"https://testnet.binance.vision"}


@pytest.mark.parametrize("url,method,headers", [
    ("https://api.binance.com/api/v3/klines", "POST", None),
    ("https://api.binance.com/api/v3/account?timestamp=1&signature=x", "GET", None),
    ("https://api.binance.com/api/v3/klines", "GET", {"X-MBX-APIKEY": "k"}),
    ("https://example.com/api/v3/klines", "GET", None),
])
def test_public_client_is_read_only_and_unsigned(url, method, headers):
    with pytest.raises(endpoints.EndpointRefused):
        endpoints.check_public(url, method, headers)


def test_public_mainnet_read_passes():
    endpoints.check_public("https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d", "GET")


def test_redirects_are_refused_not_followed():
    class Redirect(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(302)
            self.send_header("Location", "https://api.binance.com/api/v3/order")
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Redirect)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    try:
        with pytest.raises(endpoints.EndpointRefused, match="redirect 302 to api.binance.com"):
            endpoints.opener().open(urllib.request.Request(f"http://127.0.0.1:{srv.server_port}/"), timeout=5)
    finally:
        srv.server_close()


# --- credentials (RT38) ------------------------------------------------------------------------------------

def _pem(tmp_path: Path) -> tuple[Path, object]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate()
    p = tmp_path / "k.pem"
    p.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return p, key


def test_keys_load_only_in_testnet_execution_mode():
    env = {"BINANCE_TESTNET_API_KEY": "abcd1234", "BINANCE_TESTNET_API_SECRET": "s"}
    for s in (SHADOW, Settings(trading_mode="TESTNET")):
        with pytest.raises(CredentialError, match="only with TRADING_MODE=TESTNET"):
            load_testnet_credentials(s, env)
    assert load_testnet_credentials(TESTNET, env).key_type == "HMAC"


def test_mainnet_key_variables_stop_the_load():
    env = {"BINANCE_TESTNET_API_KEY": "abcd1234", "BINANCE_TESTNET_API_SECRET": "s", "BINANCE_API_KEY": "live"}
    with pytest.raises(CredentialError, match="BINANCE_API_KEY"):
        load_testnet_credentials(TESTNET, env)


def test_exactly_one_signing_secret(tmp_path):
    pem, _ = _pem(tmp_path)
    with pytest.raises(CredentialError, match="exactly one"):
        load_testnet_credentials(TESTNET, {"BINANCE_TESTNET_API_KEY": "abcd"})
    with pytest.raises(CredentialError, match="exactly one"):
        load_testnet_credentials(TESTNET, {"BINANCE_TESTNET_API_KEY": "abcd", "BINANCE_TESTNET_API_SECRET": "s",
                                           "BINANCE_TESTNET_PRIVATE_KEY_PATH": str(pem)})
    with pytest.raises(CredentialError, match="cannot read"):
        load_testnet_credentials(TESTNET, {"BINANCE_TESTNET_API_KEY": "abcd", "BINANCE_TESTNET_PRIVATE_KEY_PATH": str(tmp_path / "x")})


def test_credentials_never_print_secrets(tmp_path):
    pem, _ = _pem(tmp_path)
    c = load_testnet_credentials(TESTNET, {"BINANCE_TESTNET_API_KEY": "qehUSECRETKEY", "BINANCE_TESTNET_PRIVATE_KEY_PATH": str(pem)})
    text = repr(c) + str(c)
    assert "qehU…" in text and "SECRETKEY" not in text and "PRIVATE" not in text and c.key_type == "Ed25519"


def test_hmac_matches_binance_docs_example():
    c = TestnetCredentials("k", secret="NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j")
    payload = "symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1&recvWindow=5000&timestamp=1499827319559"
    assert c.sign(payload) == "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71"


def test_ed25519_signature_verifies(tmp_path):
    pem, key = _pem(tmp_path)
    c = TestnetCredentials("k", pem=pem.read_bytes())
    key.public_key().verify(base64.b64decode(c.sign("a=1")), b"a=1")  # type: ignore[attr-defined]


# --- clock (RT27) ------------------------------------------------------------------------------------------

def test_clock_offset_uses_the_midpoint_and_thresholds():
    r = reading(1000, 1900, 1200)
    assert r.offset_ms == 800 and r.rtt_ms == 200 and r.status is ClockStatus.WARN
    assert reading(0, 100, 0).status is ClockStatus.OK
    assert reading(0, -1000, 0).status is ClockStatus.PAUSE
    with pytest.raises(ValueError):
        reading(10, 0, 5)


# --- alerts (RT42) -----------------------------------------------------------------------------------------

class FlakySink:
    def __init__(self, fail: bool = False) -> None:
        self.fail, self.sent = fail, []

    def send(self, text: str) -> None:
        if self.fail:
            raise RuntimeError("telegram down 123456:AAAAAAAAAAAAAAAAAAAAAAAAAAAA")
        self.sent.append(text)


def _queue(tmp_path, sink, clock=None):
    logs: list[str] = []
    q = AlertQueue(tmp_path / "alerts.jsonl", sink, "pc", clock or (lambda: T0), log=logs.append)
    return q, logs


def test_alerts_queue_while_telegram_is_down_and_resend_in_order(tmp_path):
    sink = FlakySink(fail=True)
    q, logs = _queue(tmp_path, sink)
    q.push(Severity.CRITICAL, "a", "first")
    q.push(Severity.WARNING, "b", "second")
    assert [a.text for a in q.pending()] == ["first", "second"]
    assert all("AAAAAAAA" not in line for line in logs)
    sink.fail = False
    assert q.flush() == 2
    assert "first" in sink.sent[0] and "second" in sink.sent[1] and q.pending() == []


def test_alert_cooldown_per_key(tmp_path):
    now = [T0]
    sink = FlakySink()
    q, _ = _queue(tmp_path, sink, lambda: now[0])
    q.push(Severity.WARNING, "clock", "x")
    q.push(Severity.WARNING, "clock", "x")
    now[0] = T0 + timedelta(minutes=16)
    q.push(Severity.WARNING, "clock", "x")
    assert len(sink.sent) == 2


def test_telegram_errors_hide_the_token():
    assert "123456:" not in mask("https://api.telegram.org/bot123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ/sendMessage")
    assert "SECRET" not in repr(TelegramSink("SECRET", "1"))


# --- dead-man (RT41) ---------------------------------------------------------------------------------------

def test_deadman_needs_https_and_never_raises():
    with pytest.raises(ValueError):
        DeadMan("http://hc-ping.com/x")

    def down(url, timeout):
        raise OSError("no route")

    d = DeadMan("https://hc-ping.com/secret-uuid", get=down)
    assert d.ping() is False and "secret-uuid" not in repr(d)
    assert DeadMan("https://hc-ping.com/u", get=lambda u, t: 200).ping() is True


# --- worker (RT01, RT07, RT27, RT38, RT41) ----------------------------------------------------------------

@dataclass
class _Lease:
    owner: str
    token: int
    expires_at: datetime


class FakeStore:
    def __init__(self, safety: AccountSafety | None = None) -> None:
        self.holder: str | None = None
        self.token = 0
        self.down = False
        self.safety = safety or AccountSafety()

    def acquire_lease(self, owner, now, ttl):
        if self.down:
            raise ConnectionError("db down")
        if self.holder not in (None, owner):
            return None
        if self.holder is None:
            self.token += 1
            self.holder = owner
        return _Lease(owner, self.token, now + ttl)

    def load_safety(self):
        return self.safety


class FakeHttp:
    def __init__(self, server_ms: float = 0, account=(200, {"canTrade": True})) -> None:
        self.server_ms, self.account, self.urls = server_ms, account, []

    def __call__(self, url, headers):
        self.urls.append((url, headers))
        if "/api/v3/time" in url:
            return 200, {"serverTime": self.server_ms}
        return self.account


def _worker(tmp_path, *, settings=TESTNET, store=None, http=None, deadman=None, creds="default"):
    if creds == "default":
        creds = TestnetCredentials("qehUSECRETKEY", secret="s")
    http = http or FakeHttp()
    rest = TestnetRest(creds, get=http, wall_ms=lambda: 0.0)
    sink = FlakySink()
    q, _ = _queue(tmp_path, sink)
    t = [0.0]
    w = Worker(settings, WorkerConfig(owner="pc:1", heartbeat_path=tmp_path / "hb.json"), rest, q, deadman=deadman,
               store=store if store is not None else FakeStore(), now=lambda: T0, monotonic=lambda: t[0], log=lambda m: None)
    return w, sink, http, t


def test_worker_refuses_modes_it_does_not_run():
    for mode in ("BACKTEST", "HISTORICAL_REPLAY"):
        with pytest.raises(WorkerRefused):
            Worker(Settings(trading_mode=mode), WorkerConfig("o", Path("hb")), TestnetRest(None), None)  # type: ignore[arg-type]


def test_testnet_execution_needs_the_lease_and_a_key(tmp_path):
    q, _ = _queue(tmp_path, None)
    with pytest.raises(WorkerRefused, match="DATABASE_URL"):
        Worker(TESTNET, WorkerConfig("o", tmp_path / "hb"), TestnetRest(TestnetCredentials("k", secret="s")), q)
    with pytest.raises(WorkerRefused, match="credentials"):
        Worker(TESTNET, WorkerConfig("o", tmp_path / "hb"), TestnetRest(None), q, store=FakeStore())


def test_start_reports_recovering_checks_key_and_writes_heartbeat(tmp_path):
    w, sink, http, _ = _worker(tmp_path, deadman=DeadMan("https://hc-ping.com/u", get=lambda u, t: 200))
    w.start()
    hb = json.loads((tmp_path / "hb.json").read_text())
    assert hb["safety"] == "RECOVERING" and hb["lease_token"] == 1 and hb["account_ok"] is True
    assert hb["clock_status"] == "OK" and hb["signing_paused"] is False and hb["deadman_ok"] is True
    assert "worker started" in sink.sent[0] and "SECRETKEY" not in " ".join(sink.sent)
    signed = [(u, h) for u, h in http.urls if "/account" in u]
    assert signed and signed[0][0].startswith("https://testnet.binance.vision/") and "signature=" in signed[0][0]


def test_halt_latch_survives_restart(tmp_path):
    w, _, _, _ = _worker(tmp_path, store=FakeStore(AccountSafety(state=SafetyState.HALTED, epoch=3, halt_reason="dd")))
    w.start()
    assert w.state.safety == "HALTED"


def test_second_writer_is_refused(tmp_path):
    store = FakeStore()
    a, _, _, _ = _worker(tmp_path / "a", store=store)
    b, sink_b, _, _ = _worker(tmp_path / "b", store=store)
    b.cfg.owner = "vps:2"
    a.start()
    with pytest.raises(LeaseLost):
        b.start()
    assert any("another worker holds" in t for t in sink_b.sent)


def test_database_loss_stops_the_worker_with_a_critical_alert(tmp_path):
    store = FakeStore()
    w, sink, _, _ = _worker(tmp_path, store=store)
    w.start()
    store.down = True
    with pytest.raises(LeaseLost):
        w.tick()
    assert "CRITICAL" in sink.sent[-1] and "database unreachable" in sink.sent[-1]


def test_large_clock_offset_pauses_signing(tmp_path):
    http = FakeHttp(server_ms=1500)
    w, sink, _, t = _worker(tmp_path, http=http)
    w.start()
    assert w.state.signing_paused is True and any("CRITICAL" in s and "clock offset +1500" in s for s in sink.sent)
    http.server_ms = 100
    t[0] = 61
    w.tick()
    assert w.state.signing_paused is False and "clock back in range" in sink.sent[-1]


def test_bad_key_is_a_critical_credential_alert(tmp_path):
    w, sink, _, _ = _worker(tmp_path, http=FakeHttp(account=(401, {"code": -2015, "msg": "Invalid API-key"})))
    w.start()
    assert w.state.account_ok is False
    alert = next(s for s in sink.sent if "failed the account check" in s)
    assert "CRITICAL" in alert and "qehU…" in alert and "SECRETKEY" not in alert


def test_deadman_pings_on_schedule(tmp_path):
    pings: list[str] = []
    w, _, _, t = _worker(tmp_path, deadman=DeadMan("https://hc-ping.com/u", get=lambda u, _t: pings.append(u) or 200))
    w.start()
    w.tick()
    t[0] = 59
    w.tick()
    t[0] = 60
    w.tick()
    assert len(pings) == 2


def test_shadow_worker_runs_without_database_or_keys(tmp_path):
    q, _ = _queue(tmp_path, FlakySink())
    w = Worker(SHADOW, WorkerConfig("o", tmp_path / "hb.json"), TestnetRest(None, get=FakeHttp(), wall_ms=lambda: 0.0), q,
               now=lambda: T0, log=lambda m: None)
    w.start()
    w.tick()
    assert w.state.lease_token is None and w.state.account_ok is None


def test_worker_does_not_depend_on_the_web_app():
    import trading.runtime.worker as mod

    src = Path(mod.__file__).read_text()
    assert "fastapi" not in src and "from api" not in src and "import api" not in src
