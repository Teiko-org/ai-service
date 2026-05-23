"""Defaults de teste isolados do .env local.

Sem este conftest, o pytest herda valores como ALLOW_ANONYMOUS_WRITES=true do
.env do dev e mascara regressao em testes que dependem de require_auth, throttle
e tokens de confirmacao.
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.core import confirm_tokens, write_throttle
from app.core.cache import cache


@pytest.fixture(autouse=True)
def _isolated_write_settings(monkeypatch):
    # Defaults conservadores: tests rodam como producao "stricta" salvo override.
    monkeypatch.setattr(settings, "confirm_token_secret", "test-secret-only-for-tests")
    monkeypatch.setattr(settings, "confirm_token_ttl_seconds", 120)
    monkeypatch.setattr(settings, "allow_anonymous_writes", False)
    monkeypatch.setattr(settings, "enable_write_tools", False)
    yield


@pytest.fixture(autouse=True)
def _reset_runtime_stores():
    cache.clear()
    confirm_tokens._consumed_store = confirm_tokens._ConsumedTokensStore()
    confirm_tokens._issued_metadata_store = confirm_tokens._IssuedMetadataStore()
    write_throttle.write_throttle = write_throttle.WriteThrottle()
    yield
    cache.clear()
