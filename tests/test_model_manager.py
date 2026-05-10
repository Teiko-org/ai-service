"""Testes do ModelManager: selecao, fallback, cooldown, exhaustion."""

import time
from unittest.mock import patch

import pytest

from app.core.model_manager import ModelManager


@pytest.fixture
def mm():
    """Manager isolado com lista determinista para testes."""
    with patch("app.core.model_manager.FALLBACK_MODELS", ["model-b", "model-c"]):
        with patch("app.core.model_manager.settings") as s:
            s.gemini_model = "model-a"
            yield ModelManager()


def test_get_model_returns_primary(mm):
    assert mm.get_model() == "model-a"


def test_primary_in_fallback_not_duplicated():
    with patch("app.core.model_manager.FALLBACK_MODELS", ["model-a", "model-b"]):
        with patch("app.core.model_manager.settings") as s:
            s.gemini_model = "model-a"
            mgr = ModelManager()
    assert mgr._models == ["model-a", "model-b"]


def test_primary_not_in_fallback_prepended():
    with patch("app.core.model_manager.FALLBACK_MODELS", ["model-x", "model-y"]):
        with patch("app.core.model_manager.settings") as s:
            s.gemini_model = "model-z"
            mgr = ModelManager()
    assert mgr._models == ["model-z", "model-x", "model-y"]


def test_mark_rate_limited_returns_fallback(mm):
    fallback = mm.mark_rate_limited("model-a")
    assert fallback == "model-b"
    assert mm.get_model() == "model-b"


def test_mark_rate_limited_skips_other_cooldowns(mm):
    mm.mark_rate_limited("model-a")
    fallback = mm.mark_rate_limited("model-b")
    assert fallback == "model-c"


def test_all_models_in_cooldown_returns_earliest_fallback(mm):
    """Quando todos estao em cooldown, libera o que expira primeiro (nova tentativa)."""
    mm.mark_rate_limited("model-a")
    mm.mark_rate_limited("model-b")
    fallback = mm.mark_rate_limited("model-c")
    assert fallback in ("model-a", "model-b")
    assert mm.get_status()[fallback]["available"] is True


def test_get_model_when_all_cooldown_returns_primary(mm):
    mm.mark_rate_limited("model-a")
    mm.mark_rate_limited("model-b")
    mm.mark_rate_limited("model-c")
    assert mm.get_model() == "model-a"


def test_retry_after_used_for_cooldown(mm):
    mm.mark_rate_limited("model-a", retry_after=120)
    status = mm.get_status()
    assert status["model-a"]["available"] is False
    assert status["model-a"]["cooldown_remaining"] > 100


def test_retry_after_zero_uses_default(mm):
    mm.mark_rate_limited("model-a", retry_after=0)
    status = mm.get_status()
    assert status["model-a"]["cooldown_remaining"] > 60


def test_retry_after_negative_uses_default(mm):
    mm.mark_rate_limited("model-a", retry_after=-10)
    status = mm.get_status()
    assert status["model-a"]["cooldown_remaining"] > 60


def test_status_format(mm):
    status = mm.get_status()
    assert set(status.keys()) == {"model-a", "model-b", "model-c"}
    for model_status in status.values():
        assert "available" in model_status
        assert "cooldown_remaining" in model_status


def test_cooldown_expires_naturally(mm):
    with patch("time.time", return_value=1000):
        mm.mark_rate_limited("model-a", retry_after=10)
    with patch("time.time", return_value=1100):
        assert mm.get_model() == "model-a"
