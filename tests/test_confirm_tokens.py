import time

import pytest

from app.config import settings
from app.core import confirm_tokens
from app.core.confirm_tokens import (
    ConfirmTokenError,
    count_user_messages,
    ensure_user_turn_between,
    issue,
    verify_and_consume,
)


@pytest.fixture(autouse=True)
def _set_secret(monkeypatch):
    monkeypatch.setattr(settings, "confirm_token_secret", "test-secret-only-for-tests")
    monkeypatch.setattr(settings, "confirm_token_ttl_seconds", 120)
    yield


@pytest.fixture(autouse=True)
def _reset_consumed_store():
    confirm_tokens._consumed_store = confirm_tokens._ConsumedTokensStore()
    yield


def test_issue_and_verify_happy_path():
    tok = issue("create_batch", {"data_inicio": "2026-06-01", "data_fim": "2026-06-07"}, "sess-1")
    assert "." in tok.token
    verify_and_consume(
        tok.token,
        "create_batch",
        {"data_inicio": "2026-06-01", "data_fim": "2026-06-07"},
        "sess-1",
    )


def test_verify_rejects_when_args_change():
    tok = issue("create_batch", {"data_inicio": "2026-06-01"}, "sess-1")
    with pytest.raises(ConfirmTokenError, match="nao batem"):
        verify_and_consume(
            tok.token,
            "create_batch",
            {"data_inicio": "2026-06-02"},
            "sess-1",
        )


def test_verify_rejects_when_session_changes():
    tok = issue("create_batch", {"x": 1}, "sess-1")
    with pytest.raises(ConfirmTokenError):
        verify_and_consume(tok.token, "create_batch", {"x": 1}, "sess-OUTRA")


def test_verify_rejects_when_tool_name_changes():
    tok = issue("create_batch", {"x": 1}, "sess-1")
    with pytest.raises(ConfirmTokenError):
        verify_and_consume(tok.token, "delete_batch", {"x": 1}, "sess-1")


def test_verify_rejects_expired_token(monkeypatch):
    tok = issue("create_batch", {"x": 1}, "sess-1", ttl_seconds=1)
    monkeypatch.setattr(time, "time", lambda: tok.expires_at + 5)
    with pytest.raises(ConfirmTokenError, match="expirou"):
        verify_and_consume(tok.token, "create_batch", {"x": 1}, "sess-1")


def test_verify_rejects_replay_after_consume():
    tok = issue("create_batch", {"x": 1}, "sess-1")
    verify_and_consume(tok.token, "create_batch", {"x": 1}, "sess-1")
    with pytest.raises(ConfirmTokenError, match="ja foi processada"):
        verify_and_consume(tok.token, "create_batch", {"x": 1}, "sess-1")


def test_verify_rejects_empty_token():
    with pytest.raises(ConfirmTokenError, match="Aguardando"):
        verify_and_consume("", "create_batch", {}, "sess-1")


def test_verify_rejects_malformed_token():
    with pytest.raises(ConfirmTokenError, match="invalida"):
        verify_and_consume("not-a-token", "create_batch", {}, "sess-1")


def test_canonicalization_ignores_confirmed_and_token_fields():
    tok = issue("create_batch", {"x": 1, "confirmed": False}, "sess-1")
    verify_and_consume(
        tok.token,
        "create_batch",
        {"x": 1, "confirmed": True, "confirm_token": tok.token},
        "sess-1",
    )


def test_count_user_messages():
    assert count_user_messages(None) == 0
    assert count_user_messages([]) == 0
    history = [
        {"role": "user", "content": "oi"},
        {"role": "assistant", "content": "ola"},
        {"role": "user", "content": "como?"},
    ]
    assert count_user_messages(history) == 2


def test_ensure_user_turn_between_rejects_same_turn():
    history = [
        {"role": "user", "content": "msg1"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "msg2"},
    ]
    with pytest.raises(ConfirmTokenError, match="resposta explicita"):
        ensure_user_turn_between(user_msgs_at_issue=2, current_history=history)


def test_ensure_user_turn_between_accepts_new_user_msg():
    history = [
        {"role": "user", "content": "msg1"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "msg2"},
    ]
    ensure_user_turn_between(user_msgs_at_issue=1, current_history=history)
