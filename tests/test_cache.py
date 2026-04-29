"""Testes do SimpleCache: TTL, hit/miss, invalidate."""

import time
import pytest

from app.core.cache import SimpleCache


@pytest.fixture
def cache():
    return SimpleCache()


def test_get_missing_returns_none(cache):
    assert cache.get("nonexistent") is None


def test_set_and_get(cache):
    cache.set("k", "value", ttl=60)
    assert cache.get("k") == "value"


def test_complex_object(cache):
    obj = {"foo": [1, 2, 3], "bar": {"nested": True}}
    cache.set("complex", obj, ttl=60)
    assert cache.get("complex") == obj


def test_ttl_expiration(cache):
    cache.set("k", "value", ttl=0)
    time.sleep(0.01)
    assert cache.get("k") is None


def test_invalidate(cache):
    cache.set("k", "value", ttl=60)
    cache.invalidate("k")
    assert cache.get("k") is None


def test_invalidate_missing_no_error(cache):
    cache.invalidate("nonexistent")


def test_set_overwrites(cache):
    cache.set("k", "v1", ttl=60)
    cache.set("k", "v2", ttl=60)
    assert cache.get("k") == "v2"


def test_default_ttl_used():
    c = SimpleCache()
    c.set("k", "v")
    assert c.get("k") == "v"


def test_expired_entry_removed_on_get(cache):
    cache.set("k", "v", ttl=0)
    time.sleep(0.01)
    cache.get("k")
    assert "k" not in cache._store
