import io
import json
import urllib.error

import pytest

from scripts import telegram_notify as tg


def test_chunks_keep_lines_and_limit():
    text = "".join(f"line {i}\n" for i in range(50))
    parts = tg.chunks(text, limit=40)
    assert "".join(parts) == text
    assert all(len(p) <= 40 for p in parts)
    assert all(p.endswith("\n") for p in parts)


def test_chunks_split_overlong_line():
    parts = tg.chunks("x" * 95, limit=40)
    assert [len(p) for p in parts] == [40, 40, 15]


def test_chunks_drop_blank():
    assert tg.chunks("\n\n") == []


def test_error_never_contains_token(monkeypatch):
    def boom(req, timeout):
        assert "/botSECRET/" in req.full_url
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b'{"ok":false,"description":"Unauthorized"}'))

    monkeypatch.setattr(tg.urllib.request, "urlopen", boom)
    with pytest.raises(tg.TelegramError) as e:
        tg.send("SECRET", "1", "hi")
    assert "SECRET" not in str(e.value)
    assert "401" in str(e.value)


def test_main_requires_env(monkeypatch, capsys):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert tg.main([]) == 2
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert tg.main([]) == 2


def test_seen_chats(monkeypatch):
    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    body = {"ok": True, "result": [
        {"message": {"chat": {"id": 42, "type": "private", "first_name": "Note"}}},
        {"my_chat_member": {"chat": {"id": -1001, "type": "supergroup", "title": "alerts"}}},
    ]}
    monkeypatch.setattr(tg.urllib.request, "urlopen", lambda req, timeout: R(json.dumps(body).encode()))
    assert {c["id"] for c in tg.seen_chats("t")} == {42, -1001}


FAKE = "123456789:AAH" + "x" * 32


def test_mask_removes_token_and_token_shapes():
    url = f"https://api.telegram.org/bot{FAKE}/sendMessage"
    assert FAKE not in tg.mask(url) and "<redacted>" in tg.mask(url)
    assert tg.mask("plain text 12:34") == "plain text 12:34"
    assert tg.mask("short-SECRET", "short-SECRET") == "<redacted>"


def test_unexpected_error_with_url_is_masked(monkeypatch, capsys):
    def boom(req, timeout):
        raise ValueError(f"bad url {req.full_url}")

    monkeypatch.setattr(tg.urllib.request, "urlopen", boom)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", FAKE)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr("sys.stdin", io.StringIO("hi"))
    assert tg.main([]) == 1
    out = capsys.readouterr()
    assert FAKE not in out.err + out.out and "<redacted>" in out.err


def test_check_sends_nothing(monkeypatch, capsys):
    calls = []

    def fake(req, timeout):
        calls.append(req.full_url.rsplit("/", 1)[-1])
        return io.BytesIO(json.dumps({"ok": True, "result": {"username": "b"}}).encode())

    monkeypatch.setattr(tg.urllib.request, "urlopen", fake)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", FAKE)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    assert tg.main(["--check"]) == 0
    assert calls == ["getMe"] and FAKE not in capsys.readouterr().out
