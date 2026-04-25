"""Testes do SessionStore: criacao, expiracao, history limit, concorrencia."""

import time
from unittest.mock import patch

import pytest

from app.core.sessions import (
    Session,
    SessionStore,
    MAX_HISTORY_PER_SESSION,
)


@pytest.fixture
def store():
    s = SessionStore()
    yield s


def test_get_or_create_new_session(store):
    session = store.get_or_create()
    assert session.id is not None
    assert session.history == []


def test_get_or_create_returns_existing(store):
    s1 = store.get_or_create()
    s2 = store.get_or_create(s1.id)
    assert s1.id == s2.id


def test_get_or_create_with_unknown_id_creates(store):
    s = store.get_or_create("non-existent-id")
    assert s.id == "non-existent-id"


def test_session_expires_creates_new(store):
    s1 = store.get_or_create()
    with patch.object(Session, "is_expired", return_value=True):
        s2 = store.get_or_create(s1.id)
    assert s2.history == []


def test_append_persists_messages(store):
    s = store.get_or_create()
    store.append(s.id, "user", "Oi")
    store.append(s.id, "assistant", "Ola!")
    history = store.get_history(s.id)
    assert len(history) == 2
    assert history[0] == {"role": "user", "content": "Oi"}
    assert history[1] == {"role": "assistant", "content": "Ola!"}


def test_append_unknown_session_noop(store):
    store.append("ghost", "user", "test")
    assert store.get_history("ghost") == []


def test_history_capped_at_max(store):
    s = store.get_or_create()
    for i in range(MAX_HISTORY_PER_SESSION + 10):
        store.append(s.id, "user", f"msg-{i}")
    history = store.get_history(s.id, limit=200)
    assert len(history) == MAX_HISTORY_PER_SESSION
    assert history[0]["content"] == f"msg-{10}"
    assert history[-1]["content"] == f"msg-{MAX_HISTORY_PER_SESSION + 9}"


def test_history_limit_param(store):
    s = store.get_or_create()
    for i in range(20):
        store.append(s.id, "user", f"msg-{i}")
    history = store.get_history(s.id, limit=5)
    assert len(history) == 5
    assert history[-1]["content"] == "msg-19"


def test_session_touch_updates_last_active(store):
    s = store.get_or_create()
    initial = s.last_active
    time.sleep(0.01)
    store.append(s.id, "user", "test")
    assert s.last_active > initial


def test_session_is_expired_logic():
    s = Session(id="x", last_active=time.time() - 10000)
    assert s.is_expired() is True
    s2 = Session(id="y")
    assert s2.is_expired() is False
