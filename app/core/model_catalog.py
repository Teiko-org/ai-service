"""
Catalogo de modelos Gemini para texto + function calling (Kuroko).

Ordem pensada para fallback: barato/rapido -> geracoes mais novas (quota separada)
-> pro (ultimo recurso). Exclui Live, TTS, imagem, video, embedding, etc.

Referencia: https://ai.google.dev/gemini-api/docs/models
"""

from __future__ import annotations

# Modelos estaveis/GA adequados a generateContent + tools (maio/2026).
DEFAULT_GEMINI_FALLBACK_MODELS: tuple[str, ...] = (
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash",
    "gemini-2.5-pro",
)

# Prefixos de modelos que nao entram na cadeia automatica.
_EXCLUDED_PREFIXES = (
    "gemini-live-",
    "gemini-2.5-flash-native-audio",
    "gemini-2.5-flash-preview-tts",
    "gemini-2.5-pro-preview-tts",
    "gemini-3.1-flash-live",
    "gemini-3.1-flash-tts",
    "gemini-2.5-flash-image",
    "gemini-3.1-flash-image",
    "gemini-3-pro-image",
    "imagen-",
    "veo-",
    "lyria-",
    "embedding",
    "computer-use",
    "deep-research",
    "antigravity",
    "robotics",
)


def build_model_chain(primary: str, fallbacks: list[str]) -> list[str]:
    """Primario primeiro, depois fallbacks sem duplicar."""
    chain: list[str] = []
    for name in [primary, *fallbacks]:
        n = (name or "").strip()
        if not n or n in chain:
            continue
        chain.append(n)
    return chain


def is_assistant_candidate(model_id: str) -> bool:
    """Filtra modelos listados pela API que servem ao chat com tools."""
    mid = (model_id or "").strip()
    if not mid or mid.startswith("models/"):
        mid = mid.removeprefix("models/")
    low = mid.lower()
    if any(low.startswith(p) for p in _EXCLUDED_PREFIXES):
        return False
    if any(
        x in low
        for x in (
            "-image",
            "-tts",
            "-live",
            "embedding",
            "aqa",
            "exp",
        )
    ):
        return False
    return low.startswith("gemini-")


def filter_generate_content_models(model_names: list[str]) -> list[str]:
    return [m for m in model_names if is_assistant_candidate(m)]
