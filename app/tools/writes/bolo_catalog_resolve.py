"""Resolve nomes de massa/recheio para IDs (catalogo Java /bolos)."""

from __future__ import annotations

from typing import Any

import httpx

from app.tools.writes._helpers import WriteToolError, get_json
from app.tools.writes.catalog_display import (
    exclusivo_display_label,
    format_massa_options,
    format_recheio_options_help,
    massa_display_label,
    unitario_display_label,
)
from app.tools.writes.text_match import match_by_name, pick_highest_id


def _massa_label(item: dict[str, Any]) -> str:
    return massa_display_label(item)


def _exclusivo_label(item: dict[str, Any]) -> str:
    return exclusivo_display_label(item)


def _unitario_label(item: dict[str, Any]) -> str:
    return unitario_display_label(item)


def _resolve_massa_from_items(
    items: list[dict], massa_nome: str
) -> tuple[int, str] | None:
    hits = match_by_name(items, massa_nome, "sabor")
    if not hits:
        return None
    chosen = pick_highest_id(hits)
    mid = chosen.get("id") if chosen else None
    if not isinstance(mid, int) or mid <= 0:
        return None
    return mid, _massa_label(chosen)


def _resolve_recheio_exclusivo_from_items(
    items: list[dict], nome: str
) -> tuple[int, str] | None:
    hits = match_by_name(items, nome, "nome", "sabor1", "sabor2")
    if not hits:
        return None
    chosen = pick_highest_id(hits)
    eid = chosen.get("id") if chosen else None
    if not isinstance(eid, int) or eid <= 0:
        return None
    return eid, _exclusivo_label(chosen)


def _resolve_recheio_by_name_from_items(
    items_u: list[dict], items_e: list[dict], nome: str
) -> tuple[dict[str, int | None], str] | None:
    hits_u = match_by_name(items_u, nome, "sabor", "descricao")
    if hits_u:
        chosen = pick_highest_id(hits_u)
        uid = chosen.get("id") if chosen else None
        if isinstance(uid, int) and uid > 0:
            return (
                {"recheio_exclusivo_id": None, "recheio_unitario_id": uid},
                _unitario_label(chosen),
            )
    hits_e = match_by_name(items_e, nome, "nome", "sabor1", "sabor2")
    if hits_e:
        chosen = pick_highest_id(hits_e)
        eid = chosen.get("id") if chosen else None
        if isinstance(eid, int) and eid > 0:
            return (
                {"recheio_exclusivo_id": eid, "recheio_unitario_id": None},
                _exclusivo_label(chosen),
            )
    return None


async def resolve_massa_id(
    client: httpx.AsyncClient,
    base_url: str,
    token: str | None,
    massa_nome: str,
) -> tuple[int, str]:
    payload = await get_json(client, f"{base_url}/bolos/massa", token)
    items = payload if isinstance(payload, list) else []
    resolved = _resolve_massa_from_items(items, massa_nome)
    if resolved is None:
        disponiveis = format_massa_options(items)
        raise WriteToolError(
            f"Massa '{massa_nome}' nao encontrada no cadastro. "
            f"Massas disponiveis: {disponiveis}."
        )
    return resolved


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
        disponiveis = format_recheio_options_help(items, [])
        raise WriteToolError(
            f"Recheio unitario '{recheio_nome}' nao encontrado. {disponiveis}"
        )
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
    resolved = _resolve_recheio_exclusivo_from_items(items, nome)
    if resolved is None:
        disponiveis = format_recheio_options_help([], items)
        raise WriteToolError(
            f"Recheio exclusivo '{nome}' nao encontrado no catalogo. "
            f"{disponiveis}"
        )
    return resolved


async def resolve_recheio_by_name(
    client: httpx.AsyncClient,
    base_url: str,
    token: str | None,
    nome: str,
) -> tuple[dict[str, int | None], str]:
    """Unitario primeiro; se nao achar, exclusivo (nomes tipo Hugo, Dora)."""
    payload_u = await get_json(client, f"{base_url}/bolos/recheio-unitario", token)
    items_u = payload_u if isinstance(payload_u, list) else []
    payload_e = await get_json(client, f"{base_url}/bolos/recheio-exclusivo", token)
    items_e = payload_e if isinstance(payload_e, list) else []
    resolved = _resolve_recheio_by_name_from_items(items_u, items_e, nome)
    if resolved is None:
        raise WriteToolError(
            f"Recheio '{nome}' nao encontrado no cadastro. "
            f"{format_recheio_options_help(items_u, items_e)}"
        )
    return resolved


def _needs_recheio_resolution(out: dict) -> bool:
    return not any(
        out.get(k) is not None
        for k in (
            "recheio_exclusivo_id",
            "recheio_unitario_id",
            "recheio_unitario_1",
            "recheio_unitario_2",
        )
    )


async def apply_catalog_names(
    args: dict,
    client: httpx.AsyncClient,
    base_url: str,
    token: str | None,
) -> dict:
    """Preenche massa_id / recheio_* a partir de nomes; valida tudo antes de falhar."""
    out = dict(args)
    labels: dict[str, str] = dict(out.get("_catalog_labels") or {})
    errors: list[str] = []

    massa_nome = out.get("massa_nome")
    needs_massa = out.get("massa_id") is None and isinstance(massa_nome, str) and massa_nome.strip()

    recheio_exclusivo_nome = out.get("recheio_exclusivo_nome")
    recheio_nome = out.get("recheio_nome")
    needs_exclusivo = (
        out.get("recheio_exclusivo_id") is None
        and isinstance(recheio_exclusivo_nome, str)
        and recheio_exclusivo_nome.strip()
    )
    needs_recheio = _needs_recheio_resolution(out) and isinstance(
        recheio_nome, str
    ) and recheio_nome.strip() and not needs_exclusivo

    massa_items: list[dict] = []
    items_u: list[dict] = []
    items_e: list[dict] = []

    if needs_massa:
        payload = await get_json(client, f"{base_url}/bolos/massa", token)
        massa_items = payload if isinstance(payload, list) else []

    if needs_exclusivo or needs_recheio:
        payload_u = await get_json(client, f"{base_url}/bolos/recheio-unitario", token)
        items_u = payload_u if isinstance(payload_u, list) else []
        payload_e = await get_json(client, f"{base_url}/bolos/recheio-exclusivo", token)
        items_e = payload_e if isinstance(payload_e, list) else []

    if needs_massa:
        resolved = _resolve_massa_from_items(massa_items, str(massa_nome).strip())
        if resolved is None:
            disponiveis = format_massa_options(massa_items)
            errors.append(
                f"Massa '{massa_nome}' nao encontrada no cadastro. "
                f"Massas disponiveis: {disponiveis}."
            )
        else:
            mid, label = resolved
            out["massa_id"] = mid
            labels["massa"] = label

    if needs_exclusivo:
        resolved = _resolve_recheio_exclusivo_from_items(
            items_e, str(recheio_exclusivo_nome).strip()
        )
        if resolved is None:
            errors.append(
                f"Recheio exclusivo '{recheio_exclusivo_nome}' nao encontrado. "
                f"{format_recheio_options_help(items_u, items_e)}"
            )
        else:
            eid, label = resolved
            out["recheio_exclusivo_id"] = eid
            labels["recheio"] = label

    if needs_recheio:
        resolved = _resolve_recheio_by_name_from_items(
            items_u, items_e, str(recheio_nome).strip()
        )
        if resolved is None:
            errors.append(
                f"Recheio '{recheio_nome}' nao encontrado no cadastro. "
                f"{format_recheio_options_help(items_u, items_e)}"
            )
        else:
            fields, label = resolved
            out.update(fields)
            labels["recheio"] = label

    if errors:
        raise WriteToolError("\n\n".join(errors))

    if labels:
        out["_catalog_labels"] = labels
    return out
