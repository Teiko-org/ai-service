"""Testes do ApiKeyManager: selecao, fallback, cooldown."""

import time
from unittest.mock import patch

import pytest

from app.core.api_key_manager import ApiKeyManager


@pytest.fixture
def km():
    with patch("app.core.api_key_manager.settings") as s:
        s.gemini_api_keys_list = ["k1", "k2", "k3"]
        yield ApiKeyManager()


def test_get_key_index_returns_first(km):
    assert km.get_key_index() == 0


def test_mark_rate_limited_returns_next_key(km):
    fallback = km.mark_rate_limited(0)
    assert fallback == 1
    assert km.get_key_index() == 1


def test_all_keys_cooldown_releases_earliest(km):
    km.mark_rate_limited(0)
    km.mark_rate_limited(1)
    fallback = km.mark_rate_limited(2)
    assert fallback in (0, 1)
    assert km.get_status()[f"key_{fallback + 1}"]["available"] is True


def test_single_key_reduces_cooldown_and_retries(km):
    with patch("app.core.api_key_manager.settings") as s:
        s.gemini_api_keys_list = ["only"]
        mgr = ApiKeyManager()
    fallback = mgr.mark_rate_limited(0)
    assert fallback == 0


def test_status_masks_keys(km):
    status = km.get_status()
    assert set(status.keys()) == {"key_1", "key_2", "key_3"}
    for entry in status.values():
        assert "available" in entry
        assert "cooldown_remaining" in entry


def test_retry_after_used_for_cooldown(km):
    km.mark_rate_limited(0, retry_after=120)
    status = km.get_status()
    assert status["key_1"]["available"] is False
    assert status["key_1"]["cooldown_remaining"] > 100


def test_cooldown_expires_naturally(km):
    with patch("time.time", return_value=1000):
        km.mark_rate_limited(0, retry_after=10)
    with patch("time.time", return_value=1100):
        assert km.get_key_index() == 0
