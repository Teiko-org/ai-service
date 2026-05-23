"""Regra de negocio: no maximo uma fornada ativa por vez."""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from app.tools.writes._helpers import WriteToolError, get_json


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    text = str(value).strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _periods_overlap(start_a: date, end_a: date, start_b: date, end_b: date) -> bool:
    return start_a <= end_b and end_a >= start_b


def pick_active_batch(items: list[dict]) -> dict | None:
    """Fornada ativa com maior dataFim (cobre overlap e leitura de itens)."""
    actives = [i for i in items if isinstance(i, dict) and i.get("ativo") is not False]
    if not actives:
        return None
    return max(
        actives,
        key=lambda f: _parse_date(f.get("dataFim") or f.get("data_fim")) or date.min,
    )


# Alias retrocompatível com testes antigos.
_pick_active_for_message = pick_active_batch


async def find_active_batch(
    client: httpx.AsyncClient,
    base_url: str,
    token: str | None,
) -> dict | None:
    try:
        payload = await get_json(client, f"{base_url}/fornadas", token)
    except WriteToolError:
        return None

    items = payload if isinstance(payload, list) else payload.get("data", [])
    if not isinstance(items, list):
        return None
    return pick_active_batch(items)


def _format_period(batch: dict) -> str:
    start = batch.get("dataInicio") or batch.get("data_inicio") or "?"
    end = batch.get("dataFim") or batch.get("data_fim") or "?"
    return f"{str(start)[:10]} a {str(end)[:10]}"


def active_batch_error_message(existing: dict) -> str:
    fid = existing.get("id")
    period = _format_period(existing)
    return (
        f"Ja existe uma fornada ativa (#{fid}, {period}). "
        "Para abrir outra com essas datas, peca ao usuario se quer substituir a atual "
        "(replace_active_batch) ou encerrar antes (close_batch). "
        "Nao use create_batch de novo ate resolver a ativa."
    )
