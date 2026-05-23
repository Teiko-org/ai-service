#!/usr/bin/env python3
"""
Lista modelos Gemini disponiveis na sua API key (generateContent / chat).

Uso:
  cd ai-service
  .venv\\Scripts\\python.exe scripts\\list_gemini_models.py
  .venv\\Scripts\\python.exe scripts\\list_gemini_models.py --suggest-chain
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.core.gemini import get_client  # noqa: E402
from app.core.model_catalog import (  # noqa: E402
    DEFAULT_GEMINI_FALLBACK_MODELS,
    filter_generate_content_models,
    is_assistant_candidate,
)


def _model_id(raw: object) -> str:
    name = getattr(raw, "name", None) or str(raw)
    return name.removeprefix("models/")


def _supports_generate_content(raw: object) -> bool:
    methods = getattr(raw, "supported_actions", None) or getattr(
        raw, "supported_generation_methods", None
    )
    if not methods:
        return True
    text = " ".join(str(m).lower() for m in methods)
    return "generatecontent" in text.replace("_", "").replace("-", "")


def fetch_models() -> list[str]:
    client = get_client(0)
    out: list[str] = []
    for m in client.models.list():
        mid = _model_id(m)
        if not is_assistant_candidate(mid):
            continue
        if not _supports_generate_content(m):
            continue
        out.append(mid)
    return sorted(set(out))


def main() -> int:
    p = argparse.ArgumentParser(description="Lista modelos Gemini para o Kuroko")
    p.add_argument(
        "--suggest-chain",
        action="store_true",
        help="Sugere GEMINI_MODEL + GEMINI_FALLBACK_MODELS com base no catalogo",
    )
    args = p.parse_args()

    if not settings.gemini_api_keys_list:
        print("Configure GEMINI_API_KEY ou GEMINI_API_KEYS no .env", file=sys.stderr)
        return 1

    try:
        available = fetch_models()
    except Exception as exc:  # noqa: BLE001
        print(f"Erro ao listar modelos: {exc}", file=sys.stderr)
        return 1

    print(f"Chave: #{1} de {len(settings.gemini_api_keys_list)}")
    print(f"Modelos chat/tools ({len(available)}):\n")
    for mid in available:
        print(f"  - {mid}")

    print("\nCadeia padrao do projeto (app/core/model_catalog.py):")
    for mid in DEFAULT_GEMINI_FALLBACK_MODELS:
        mark = "ok" if mid in available else "nao listado na API"
        print(f"  - {mid} [{mark}]")

    if args.suggest_chain:
        preferred = [
            settings.gemini_model,
            *DEFAULT_GEMINI_FALLBACK_MODELS,
        ]
        chain = [m for m in preferred if m in available]
        for m in available:
            if m not in chain and "flash" in m and "lite" in m:
                chain.append(m)
        if not chain and available:
            chain = available[:5]
        primary = chain[0] if chain else settings.gemini_model
        fallbacks = [m for m in chain[1:] if m != primary]
        print("\nSugestao .env:")
        print(f"GEMINI_MODEL={primary}")
        if fallbacks:
            print(f"GEMINI_FALLBACK_MODELS={','.join(fallbacks)}")

    filtered = filter_generate_content_models(available)
    print(f"\nFiltrados para assistente: {len(filtered)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
