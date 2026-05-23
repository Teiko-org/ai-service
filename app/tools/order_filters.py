"""Filtros de pedidos com datas BR, massa por nome e fallback de entrega."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import httpx

from app.core.llm_present import trim_orders_for_llm
from app.tools.writes._helpers import WriteToolError, get_json
from app.tools.writes.dates import parse_user_date
from app.tools.writes.text_match import match_by_name, normalize_text, pick_highest_id

# Maximo de chamadas GET /por-massa/{id} por pergunta (evita timeout no app).
_MAX_MASSA_IDS_PER_QUERY = 2
_MASSA_FETCH_TIMEOUT = httpx.Timeout(20.0, connect=5.0)


def parse_delivery_date_arg(value: Any) -> tuple[date | None, dict[str, Any] | None]:
    if value is None or value == "":
        return None, {"error": "delivery_date e obrigatorio (dd/MM/yyyy ou yyyy-MM-dd)."}
    try:
        return parse_user_date(str(value), "delivery_date"), None
    except WriteToolError as exc:
        return None, {"error": str(exc)}


def _api_value_to_date(value: Any) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if "T" in text:
        text = text.split("T", 1)[0]
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _resumo_row_from_api(item: dict, *, cliente: str | None = None) -> dict:
    row = {
        "id": item.get("id"),
        "status": item.get("status"),
        "valorPedido": item.get("valor"),
        "dataPedido": item.get("dataPedido"),
        "dataEntrega": item.get("dataEntrega"),
        "tipoProduto": "BOLO",
        "nomeDoCliente": cliente or item.get("nomeCliente") or item.get("nomeDoCliente"),
    }
    return row


def _detail_previsao_date(detail: dict) -> date | None:
    for key in ("dataPrevisaoEntrega", "data_previsao_entrega"):
        found = _api_value_to_date(detail.get(key))
        if found:
            return found
    for nested in ("pedido", "pedidoBolo"):
        block = detail.get(nested)
        if isinstance(block, dict):
            for key in ("dataPrevisaoEntrega", "data_previsao_entrega"):
                found = _api_value_to_date(block.get(key))
                if found:
                    return found
    return None


def _dedupe_resumos_by_pedido_bolo(items: list[dict]) -> list[dict]:
    """Um resumo por pedidoBoloId, preferindo o maior id de resumo (mais recente)."""
    by_pb: dict[int, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        pb_id = item.get("pedidoBoloId")
        if not isinstance(pb_id, int) or pb_id <= 0:
            continue
        prev = by_pb.get(pb_id)
        if prev is None or (item.get("id") or 0) > (prev.get("id") or 0):
            by_pb[pb_id] = item
    return sorted(by_pb.values(), key=lambda row: row.get("id") or 0, reverse=True)


async def _fetch_orders_by_mass_id(
    client: httpx.AsyncClient,
    base_url: str,
    token: str | None,
    massa_id: int,
    status: str | None,
    headers: dict,
) -> list[dict]:
    params: dict[str, str] = {}
    if status:
        params["status"] = status
    url = f"{base_url}/resumo-pedido/pedido-bolo/por-massa/{massa_id}"
    resp = await client.get(
        url, params=params or None, headers=headers, timeout=_MASSA_FETCH_TIMEOUT
    )
    if resp.status_code == 204:
        return []
    resp.raise_for_status()
    raw = resp.json()
    return raw if isinstance(raw, list) else []


def _match_mass_items(items: list[dict], massa_nome: str) -> list[dict]:
    """Match exato no sabor (evita 'cacau' disparar dezenas de ids / cacau_expresso)."""
    norm_q = normalize_text(massa_nome)
    if not norm_q:
        return []
    exact = [
        item
        for item in items
        if isinstance(item, dict)
        and normalize_text(str(item.get("sabor") or "")) == norm_q
    ]
    if exact:
        return exact
    # Fallback leve: query multi-palavra (ex.: "cacau expresso" -> cacau_expresso)
    if " " in norm_q:
        underscored = norm_q.replace(" ", "_")
        return [
            item
            for item in items
            if isinstance(item, dict)
            and normalize_text(str(item.get("sabor") or "")) == underscored
        ]
    return []


async def resolve_mass_ids_for_name(
    client: httpx.AsyncClient,
    base_url: str,
    token: str | None,
    massa_nome: str,
) -> tuple[list[int], dict[str, Any] | None]:
    payload = await get_json(client, f"{base_url}/bolos/massa", token)
    items = payload if isinstance(payload, list) else []
    hits = _match_mass_items(items, massa_nome)
    if not hits:
        return [], {
            "error": (
                f"Massa '{massa_nome}' nao encontrada. "
                "Use get_doughs_catalog para ver ids e nomes."
            )
        }
    by_sabor: dict[str, list[dict]] = {}
    for item in hits:
        key = normalize_text(str(item.get("sabor") or ""))
        by_sabor.setdefault(key, []).append(item)
    ids: list[int] = []
    for group in list(by_sabor.values())[:_MAX_MASSA_IDS_PER_QUERY]:
        chosen = pick_highest_id(group)
        if not chosen:
            continue
        mid = chosen.get("id")
        if isinstance(mid, int) and mid > 0:
            ids.append(mid)
    return sorted(set(ids)), None


async def fetch_orders_by_mass_ids(
    client: httpx.AsyncClient,
    base_url: str,
    token: str | None,
    massa_ids: list[int],
    status: str | None,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    ids = massa_ids[:_MAX_MASSA_IDS_PER_QUERY]
    batches = await asyncio.gather(
        *(
            _fetch_orders_by_mass_id(client, base_url, token, mid, status, headers)
            for mid in ids
        )
    )
    merged: list[dict] = []
    seen: set[Any] = set()
    for rows in batches:
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            oid = row.get("id")
            if oid in seen:
                continue
            seen.add(oid)
            merged.append(row)
    result = trim_orders_for_llm(merged)
    if len(massa_ids) > len(ids):
        note = (
            f"Busca limitada aos {len(ids)} id(s) canonicos de massa "
            f"(cadastro tinha {len(massa_ids)} ids duplicados)."
        )
        result["message"] = (
            f"{result['message']} {note}" if result.get("message") else note
        )
    return result


async def _fetch_previsao_for_resumo(
    client: httpx.AsyncClient,
    base_url: str,
    item: dict,
    headers: dict,
) -> tuple[dict, date | None, str | None]:
    pb_id = item.get("pedidoBoloId")
    if not isinstance(pb_id, int) or pb_id <= 0:
        return item, None, None
    try:
        det_resp = await client.get(
            f"{base_url}/resumo-pedido/pedido-bolo/detalhe/{pb_id}",
            headers=headers,
            timeout=_MASSA_FETCH_TIMEOUT,
        )
    except httpx.HTTPError:
        return item, None, None
    if det_resp.status_code != 200:
        return item, None, None
    body = det_resp.json()
    if not isinstance(body, dict):
        return item, None, None
    cliente = body.get("nomeCliente")
    if isinstance(cliente, str):
        cliente = cliente.strip() or None
    return item, _detail_previsao_date(body), cliente


async def _fallback_orders_by_delivery_date(
    client: httpx.AsyncClient,
    base_url: str,
    token: str | None,
    target: date,
    status: str | None,
    headers: dict,
) -> list[dict]:
    """Confere dataPrevisaoEntrega no pedido quando resumo.dataEntrega falha."""
    resp = await client.get(
        f"{base_url}/resumo-pedido/pedido-bolo",
        headers=headers,
        timeout=_MASSA_FETCH_TIMEOUT,
    )
    if resp.status_code == 204:
        return []
    resp.raise_for_status()
    raw = resp.json()
    if not isinstance(raw, list):
        return []

    matched: list[dict] = []
    seen_resumo: set[Any] = set()
    need_detail: list[dict] = []

    for item in raw:
        if not isinstance(item, dict):
            continue
        if status and str(item.get("status", "")).upper() != status:
            continue
        rid = item.get("id")
        if rid in seen_resumo:
            continue
        seen_resumo.add(rid)
        resumo_date = _api_value_to_date(item.get("dataEntrega"))
        if resumo_date == target:
            matched.append(_resumo_row_from_api(item))
            continue
        need_detail.append(item)

    candidates = _dedupe_resumos_by_pedido_bolo(need_detail)[:80]

    async def _check(item: dict) -> dict | None:
        resumo_item, previsao, cliente = await _fetch_previsao_for_resumo(
            client, base_url, item, headers
        )
        if previsao != target:
            return None
        return _resumo_row_from_api(resumo_item, cliente=cliente)

    chunk_size = 12
    for start in range(0, len(candidates), chunk_size):
        chunk = candidates[start : start + chunk_size]
        rows = await asyncio.gather(*[_check(item) for item in chunk])
        for row in rows:
            if row is not None:
                matched.append(row)
    return matched


async def fetch_orders_by_delivery_date(
    client: httpx.AsyncClient,
    base_url: str,
    token: str | None,
    delivery_date_raw: Any,
    status: str | None,
) -> dict[str, Any]:
    target, err = parse_delivery_date_arg(delivery_date_raw)
    if err:
        return err
    assert target is not None

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    params: dict[str, str] = {"dataEntrega": target.isoformat()}
    if status:
        params["status"] = status

    url = f"{base_url}/resumo-pedido/pedido-bolo/por-data-entrega"
    resp = await client.get(url, params=params, headers=headers)
    if resp.status_code == 204:
        raw: list = []
    else:
        resp.raise_for_status()
        body = resp.json()
        raw = body if isinstance(body, list) else []

    if not raw:
        raw = await _fallback_orders_by_delivery_date(
            client, base_url, token, target, status, headers
        )
        result = trim_orders_for_llm(raw)
        if not result["data"]:
            result["message"] = (
                f"Nenhum pedido de bolo com entrega em {target.strftime('%d/%m/%Y')}."
            )
        else:
            result["message"] = (
                f"Pedidos com entrega em {target.strftime('%d/%m/%Y')} "
                f"(busca ampliada pela data do pedido de bolo)."
            )
        return result

    return trim_orders_for_llm(raw)
