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
