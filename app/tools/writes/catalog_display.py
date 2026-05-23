"""Rotulos legiveis para massas e recheios (evita snake_case cru na UX)."""

from __future__ import annotations

from typing import Any


def format_display_token(raw: str) -> str:
    """Ex.: cacau_expresso -> Cacau expresso."""
    text = (raw or "").strip()
    if not text:
        return ""
    text = text.replace("_", " ")
    return " ".join(part.capitalize() for part in text.split())


def massa_display_label(item: dict[str, Any]) -> str:
    raw = item.get("sabor") or item.get("nome") or f"#{item.get('id')}"
    return format_display_token(str(raw))


def unitario_display_label(item: dict[str, Any]) -> str:
    desc = item.get("descricao")
    if isinstance(desc, str) and desc.strip():
        cleaned = format_display_token(desc)
        if cleaned:
            return cleaned
    return format_display_token(str(item.get("sabor") or f"#{item.get('id')}"))


def exclusivo_display_label(item: dict[str, Any]) -> str:
    nome = item.get("nome")
    s1 = format_display_token(str(item.get("sabor1") or ""))
    s2 = format_display_token(str(item.get("sabor2") or ""))
    if isinstance(nome, str) and nome.strip():
        base = nome.strip()
        if s1 and s2:
            return f"{base} ({s1} + {s2})"
        return base
    if s1 and s2:
        return f"{s1} + {s2}"
    return f"#{item.get('id')}"


def format_unique_labels(
    labels: list[str], *, limit: int = 40, joiner: str = ", "
) -> str:
    unique: list[str] = []
    seen: set[str] = set()
    for raw in labels:
        text = str(raw).strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(text)
    if not unique:
        return "(nenhum cadastrado)"
    if len(unique) <= limit:
        return joiner.join(unique)
    head = joiner.join(unique[:limit])
    return f"{head}{joiner}e mais {len(unique) - limit}"


def format_massa_options(items: list[dict[str, Any]], *, limit: int = 20) -> str:
    labels = [massa_display_label(i) for i in items if isinstance(i, dict)]
    return format_unique_labels(labels, limit=limit)


def format_recheio_options_help(
    items_u: list[dict[str, Any]],
    items_e: list[dict[str, Any]],
    *,
    unitario_limit: int = 14,
) -> str:
    exclusivos = [
        exclusivo_display_label(i) for i in items_e if isinstance(i, dict)
    ]
    unitarios = sorted(
        {
            unitario_display_label(i)
            for i in items_u
            if isinstance(i, dict)
        },
        key=str.casefold,
    )
    parts: list[str] = []
    if exclusivos:
        parts.append(
            "Combinacoes nomeadas: "
            + format_unique_labels(exclusivos, limit=30)
        )
    if unitarios:
        parts.append(
            "Sabores avulsos: "
            + format_unique_labels(unitarios, limit=unitario_limit)
        )
    if not parts:
        return "Nenhum recheio cadastrado."
    return " ".join(parts)


def _safe_int(value: Any) -> int:
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


def product_display_name(row: dict[str, Any]) -> str:
    produto = str(row.get("produto") or "?").strip()
    cat = row.get("categoria")
    if isinstance(cat, str) and cat.strip():
        return f"{produto} ({cat.strip()})"
    return produto


def merge_fornada_product_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Une linhas repetidas do mesmo produto/categoria (soma quantidades)."""
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        produto = str(row.get("produto") or row.get("nome") or "?").strip()
        categoria = str(row.get("categoria") or "").strip()
        key = (produto.casefold(), categoria.casefold())
        qty = _safe_int(row.get("quantidade"))
        sold = _safe_int(row.get("quantidade_vendida"))
        if key not in merged:
            order.append(key)
            merged[key] = {
                "produto": produto,
                "categoria": categoria or None,
                "quantidade": qty,
                "quantidade_vendida": sold,
                "valor": row.get("valor"),
            }
            continue
        entry = merged[key]
        entry["quantidade"] = _safe_int(entry.get("quantidade")) + qty
        entry["quantidade_vendida"] = _safe_int(entry.get("quantidade_vendida")) + sold
        if entry.get("valor") is None and row.get("valor") is not None:
            entry["valor"] = row.get("valor")
    return [merged[k] for k in order]


def aggregate_fornada_products(
    produtos: list[dict[str, Any]],
) -> list[tuple[str, int]]:
    merged = merge_fornada_product_rows(produtos)
    pairs = [
        (product_display_name(row), _safe_int(row.get("quantidade")))
        for row in merged
    ]
    return sorted(pairs, key=lambda pair: pair[0].casefold())
