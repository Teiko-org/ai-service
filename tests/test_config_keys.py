"""Parsing de chaves Gemini em Settings."""

from app.config import Settings


def test_gemini_api_keys_splits_comma():
    s = Settings(
        gemini_api_keys="key-a,key-b, key-c",
        carambolos_api_url="http://localhost:8080",
    )
    assert s.gemini_api_keys_list == ["key-a", "key-b", "key-c"]


def test_gemini_api_key_with_commas_splits():
    s = Settings.model_construct(
        gemini_api_key="only-one,second,third",
        gemini_api_keys="",
    )
    assert s.gemini_api_keys_list == ["only-one", "second", "third"]
