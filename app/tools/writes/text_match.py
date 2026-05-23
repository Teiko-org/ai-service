"""Normalizacao de texto e fuzzy match para resolvers de catalogo."""

from __future__ import annotations

import re
import unicodedata
from typing import Any


def normalize_text(value: str) -> str:
    """Sem acentos, minusculas, so alfanumerico e espacos."""
    text = unicodedata.normalize("NFKD", value)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def match_by_name(items: list[dict], query: str, *name_fields: str) -> list[dict]:
    """Filtra `items` por correspondencia exata/contida em algum `name_fields`."""
    norm_q = normalize_text(query)
    if not norm_q:
        return []
    hits: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        for field in name_fields:
            raw = item.get(field)
            if not isinstance(raw, str):
                continue
            norm_item = normalize_text(raw)
            if norm_q == norm_item or norm_q in norm_item or norm_item in norm_q:
                hits.append(item)
                break
    return hits


def pick_highest_id(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Quando ha duplicatas no cadastro, prefere o maior id (mais recente)."""
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    with_id = [c for c in candidates if isinstance(c.get("id"), int)]
    if not with_id:
        return candidates[0]
    return max(with_id, key=lambda c: c["id"])
