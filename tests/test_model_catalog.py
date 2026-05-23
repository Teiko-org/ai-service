"""Catalogo e cadeia de modelos."""

from app.config import Settings
from app.core.model_catalog import (
    DEFAULT_GEMINI_FALLBACK_MODELS,
    build_model_chain,
    is_assistant_candidate,
)


def test_build_model_chain_dedupes():
    chain = build_model_chain(
        "gemini-2.5-flash-lite",
        ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro"],
    )
    assert chain == [
        "gemini-2.5-flash-lite",
        "gemini-2.5-flash",
        "gemini-2.5-pro",
    ]


def test_is_assistant_candidate_excludes_live_and_image():
    assert is_assistant_candidate("gemini-2.5-flash") is True
    assert is_assistant_candidate("gemini-2.5-flash-image") is False
    assert is_assistant_candidate("gemini-live-2.5-flash-native-audio") is False


def test_settings_fallback_models_from_env():
    s = Settings.model_construct(
        gemini_model="gemini-2.5-flash",
        gemini_fallback_models="gemini-3.5-flash,gemini-2.5-pro",
        gemini_api_keys="k1",
    )
    assert s.fallback_models_list == ["gemini-3.5-flash", "gemini-2.5-pro"]
    assert s.model_chain == [
        "gemini-2.5-flash",
        "gemini-3.5-flash",
        "gemini-2.5-pro",
    ]


def test_default_fallback_has_multiple_generations():
    assert "gemini-2.5-flash-lite" in DEFAULT_GEMINI_FALLBACK_MODELS
    assert "gemini-3.5-flash" in DEFAULT_GEMINI_FALLBACK_MODELS
