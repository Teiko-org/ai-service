"""Resolve nomes de massa/recheio para IDs (catalogo Java /bolos)."""

from __future__ import annotations

from typing import Any

import httpx

from app.tools.writes._helpers import WriteToolError, get_json
from app.tools.writes.text_match import match_by_name, pick_highest_id


def _massa_label(item: dict[str, Any]) -> str:
    return str(item.get("sabor") or item.get("nome") or f"#{item.get('id')}")


def _exclusivo_label(item: dict[str, Any]) -> str:
    nome = item.get("nome")
    if isinstance(nome, str) and nome.strip():
        return nome.strip()
    s1, s2 = item.get("sabor1"), item.get("sabor2")
    if isinstance(s1, str) and isinstance(s2, str):
        return f"{s1} com {s2}"
    return f"#{item.get('id')}"


def _unitario_label(item: dict[str, Any]) -> str:
    return str(item.get("sabor") or item.get("descricao") or f"#{item.get('id')}")


async def resolve_massa_id(
    client: httpx.AsyncClient,
    base_url: str,
    token: str | None,
    massa_nome: str,
) -> tuple[int, str]:
    payload = await get_json(client, f"{base_url}/bolos/massa", token)
    items = payload if isinstance(payload, list) else []
    hits = match_by_name(items, massa_nome, "sabor")
    if not hits:
        raise WriteToolError(
            f"Massa '{massa_nome}' nao encontrada. Use get_doughs_catalog para listar sabores."
        )
    chosen = pick_highest_id(hits)
    mid = chosen.get("id") if chosen else None
    if not isinstance(mid, int) or mid <= 0:
        raise WriteToolError("Catalogo de massa retornou id invalido.")
    return mid, _massa_label(chosen)


async def resolve_recheio_unitario_id(
    client: httpx.AsyncClient,
    base_url: str,
    token: str | None,
    recheio_nome: str,
) -> tuple[int, str]:
    payload = await get_json(client, f"{base_url}/bolos/recheio-unitario", token)
    items = payload if isinstance(payload, list) else []
    hits = match_by_name(items, recheio_nome, "sabor", "descricao")
    if not hits:
        raise WriteToolError(f"Recheio unitario '{recheio_nome}' nao encontrado.")
    chosen = pick_highest_id(hits)
    rid = chosen.get("id") if chosen else None
    if not isinstance(rid, int) or rid <= 0:
        raise WriteToolError("Catalogo de recheio retornou id invalido.")
    return rid, _unitario_label(chosen)


async def resolve_recheio_exclusivo_id(
    client: httpx.AsyncClient,
    base_url: str,
    token: str | None,
    nome: str,
) -> tuple[int, str]:
    payload = await get_json(client, f"{base_url}/bolos/recheio-exclusivo", token)
    items = payload if isinstance(payload, list) else []
    hits = match_by_name(items, nome, "nome", "sabor1", "sabor2")
    if not hits:
        raise WriteToolError(
            f"Recheio exclusivo '{nome}' nao encontrado no catalogo."
        )
    chosen = pick_highest_id(hits)
    eid = chosen.get("id") if chosen else None
    if not isinstance(eid, int) or eid <= 0:
        raise WriteToolError("Catalogo de recheio exclusivo retornou id invalido.")
    return eid, _exclusivo_label(chosen)


async def resolve_recheio_by_name(
    client: httpx.AsyncClient,
    base_url: str,
    token: str | None,
    nome: str,
) -> tuple[dict[str, int | None], str]:
    """Unitario primeiro; se nao achar, exclusivo (nomes tipo Hugo, Dora)."""
    payload_u = await get_json(client, f"{base_url}/bolos/recheio-unitario", token)
    items_u = payload_u if isinstance(payload_u, list) else []
    hits_u = match_by_name(items_u, nome, "sabor", "descricao")
    if hits_u:
        chosen = pick_highest_id(hits_u)
        uid = chosen.get("id") if chosen else None
        if isinstance(uid, int) and uid > 0:
            return (
                {"recheio_exclusivo_id": None, "recheio_unitario_id": uid},
                _unitario_label(chosen),
            )
    eid, label = await resolve_recheio_exclusivo_id(client, base_url, token, nome)
    return (
        {"recheio_exclusivo_id": eid, "recheio_unitario_id": None},
        label,
    )


async def apply_catalog_names(
    args: dict,
    client: httpx.AsyncClient,
    base_url: str,
    token: str | None,
) -> dict:
    """Preenche massa_id / recheio_* a partir de nomes; guarda rotulos para a previa."""
    out = dict(args)
    labels: dict[str, str] = dict(out.get("_catalog_labels") or {})

    if out.get("massa_id") is None and out.get("massa_nome"):
        mid, label = await resolve_massa_id(
            client, base_url, token, str(out["massa_nome"])
        )
        out["massa_id"] = mid
        labels["massa"] = label
    elif isinstance(out.get("massa_nome"), str):
        labels.setdefault("massa", str(out["massa_nome"]).strip())

    if out.get("recheio_exclusivo_id") is None and out.get("recheio_exclusivo_nome"):
        eid, label = await resolve_recheio_exclusivo_id(
            client, base_url, token, str(out["recheio_exclusivo_nome"])
        )
        out["recheio_exclusivo_id"] = eid
        labels["recheio"] = label
    elif isinstance(out.get("recheio_exclusivo_nome"), str):
        labels.setdefault("recheio", str(out["recheio_exclusivo_nome"]).strip())

    has_recheio_id = any(
        out.get(k) is not None
        for k in (
            "recheio_exclusivo_id",
            "recheio_unitario_id",
            "recheio_unitario_1",
            "recheio_unitario_2",
        )
    )
    if not has_recheio_id and out.get("recheio_nome"):
        fields, label = await resolve_recheio_by_name(
            client, base_url, token, str(out["recheio_nome"])
        )
        out.update(fields)
        labels["recheio"] = label
    elif isinstance(out.get("recheio_nome"), str):
        labels.setdefault("recheio", str(out["recheio_nome"]).strip())

    if labels:
        out["_catalog_labels"] = labels
    return out
