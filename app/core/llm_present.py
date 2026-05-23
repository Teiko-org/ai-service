"""Normaliza payloads de tools antes do Gemini (legivel, curto, consistente)."""

from __future__ import annotations

from typing import Any

from app.core.batch_status import (
    active_batch_instruction,
    build_batch_summary,
    next_batch_instruction,
)
from app.tools.writes.catalog_display import (
    exclusivo_display_label,
    format_display_token,
    massa_display_label,
    unitario_display_label,
)
from app.tools.writes.text_match import pick_highest_id

DEFAULT_ORDER_LIMIT = 8

_PRESENT_LIST_INSTRUCTION = (
    "Formate para o usuario: um item por linha, linha em branco entre itens, "
    "sem JSON nem 'id: X, sabor: Y'. Use os campos ja legiveis do payload."
)

_ORDER_LINE_SEP = " · "

_PRESENT_ORDERS_INSTRUCTION = (
    "Liste cada pedido copiando o campo `linha` (um item por linha, linha em "
    "branco entre pedidos). NAO remonte com tracos (—) nem junte campos vazios. "
    "Se truncated=true, diga o total e mostre so os itens em data."
)

_STATUS_LABELS = {
    "PENDENTE": "Pendente",
    "PAGO": "Pago",
    "CONCLUIDO": "Concluido",
    "CANCELADO": "Cancelado",
}


def format_status_label(raw: Any) -> str:
    text = str(raw or "").strip().upper()
    return _STATUS_LABELS.get(text, format_display_token(text))


def format_money_br(value: Any) -> str | None:
    if value is None:
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    return f"R$ {amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def format_date_short(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if "T" in text:
        text = text.split("T", 1)[0]
    parts = text.split("-")
    if len(parts) == 3 and len(parts[0]) == 4:
        y, m, d = parts
        return f"{d}/{m}/{y}"
    return text[:10]


def _order_sort_key(item: dict) -> str:
    for field in ("dataPedido", "dataEntrega", "data_pedido"):
        value = item.get(field)
        if value:
            return str(value)
    return ""


def _dedupe_orders_by_id(orders: list) -> list[dict]:
    seen: set[Any] = set()
    unique: list[dict] = []
    for item in orders:
        if not isinstance(item, dict):
            continue
        order_id = item.get("id")
        if order_id in seen:
            continue
        seen.add(order_id)
        unique.append(item)
    return unique


def format_order_line(row: dict) -> str:
    """Uma linha legivel; omite campos vazios (evita '— —' na resposta)."""
    parts: list[str] = []
    num = row.get("pedido_numero") or row.get("id")
    produto = row.get("produto")
    if num is not None:
        head = f"Pedido #{num}"
        if produto:
            head += f" ({produto})"
        parts.append(head)
    for key in ("cliente", "status", "valor", "entrega", "data"):
        val = row.get(key)
        if val is None:
            continue
        text = str(val).strip()
        if text:
            parts.append(text)
    if parts:
        return _ORDER_LINE_SEP.join(parts)
    return f"Pedido #{num}" if num is not None else "Pedido"


def simplify_order_row(item: dict) -> dict[str, Any]:
    if "pedido_numero" in item and "linha" in item:
        return {k: v for k, v in item.items() if v is not None}

    produto = item.get("tipoProduto") or item.get("tipo_produto") or item.get("tipo")
    tipo_entrega = item.get("tipoDoPedido") or item.get("tipo_entrega")
    row: dict[str, Any] = {
        "pedido_numero": item.get("pedido_numero") or item.get("id"),
        "cliente": (
            item.get("nomeDoCliente")
            or item.get("nome_cliente")
            or item.get("nomeCliente")
            or item.get("nome")
        ),
        "status": format_status_label(item.get("status")),
        "valor": format_money_br(item.get("valorPedido") or item.get("valor")),
        "entrega": format_display_token(str(tipo_entrega or "")),
        "data": format_date_short(item.get("dataPedido") or item.get("dataEntrega")),
    }
    if produto:
        row["produto"] = format_display_token(str(produto))
    entrega = row.get("entrega")
    if entrega is not None and not str(entrega).strip():
        del row["entrega"]
    cleaned = {k: v for k, v in row.items() if v is not None}
    cleaned["linha"] = format_order_line(cleaned)
    return cleaned


def trim_orders_for_llm(
    orders: list, *, limit: int = DEFAULT_ORDER_LIMIT, simplify: bool = True
) -> dict[str, Any]:
    if not orders:
        return {"data": [], "total": 0, "returned": 0, "truncated": False}

    unique = _dedupe_orders_by_id(orders)
    sorted_orders = sorted(unique, key=_order_sort_key, reverse=True)
    total = len(sorted_orders)
    page = sorted_orders[:limit]
    if simplify:
        page = [simplify_order_row(o) for o in page]
    truncated = total > len(page)
    result: dict[str, Any] = {
        "data": page,
        "total": total,
        "returned": len(page),
        "truncated": truncated,
        "instruction": _PRESENT_ORDERS_INSTRUCTION,
    }
    if truncated:
        result["message"] = (
            f"Mostrando os {len(page)} pedidos mais recentes "
            f"(de {total} no sistema). Pergunte com filtro se precisar de mais."
        )
    return result


def _dedupe_catalog_items(
    items: list[dict], *name_fields: str
) -> list[dict]:
    buckets: dict[str, list[dict]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        label_key = ""
        for field in name_fields:
            raw = item.get(field)
            if isinstance(raw, str) and raw.strip():
                label_key = raw.strip().casefold()
                break
        if not label_key:
            label_key = f"#{item.get('id')}"
        buckets.setdefault(label_key, []).append(item)
    out: list[dict] = []
    for group in buckets.values():
        chosen = pick_highest_id(group) or group[0]
        out.append(chosen)
    return sorted(out, key=lambda i: (i.get("id") if isinstance(i.get("id"), int) else 0))


def _format_doughs(items: list[dict]) -> dict[str, Any]:
    deduped = _dedupe_catalog_items(items, "sabor", "nome")
    rows = [
        {"id": i.get("id"), "nome": massa_display_label(i)} for i in deduped
    ]
    dup = len(items) - len(rows)
    note = (
        f" No cadastro havia {len(items)} registros; "
        f"mostrando {len(rows)} sabores distintos"
        + (f" ({dup} duplicatas de id antigas ocultadas)." if dup > 0 else ".")
    )
    return {
        "itens": rows,
        "total_registros_api": len(items),
        "sabores_distintos": len(rows),
        "instruction": (
            _PRESENT_LIST_INSTRUCTION
            + " Massas: linha '#{id} · {nome}' (ex.: '#149 · Cacau')."
            + note
        ),
    }


def _format_unitario_fillings(items: list[dict]) -> dict[str, Any]:
    deduped = _dedupe_catalog_items(items, "sabor", "descricao")
    rows = [
        {"id": i.get("id"), "nome": unitario_display_label(i)} for i in deduped
    ]
    return {
        "itens": rows,
        "total_registros_api": len(items),
        "sabores_distintos": len(rows),
        "instruction": (
            _PRESENT_LIST_INSTRUCTION
            + " Recheios avulsos: '#{id} · {nome}'."
        ),
    }


def _format_exclusive_fillings(items: list[dict]) -> dict[str, Any]:
    deduped = _dedupe_catalog_items(items, "nome", "sabor1")
    rows = [
        {"id": i.get("id"), "nome": exclusivo_display_label(i)} for i in deduped
    ]
    return {
        "itens": rows,
        "total_registros_api": len(items),
        "combinacoes": len(rows),
        "instruction": (
            _PRESENT_LIST_INSTRUCTION
            + " Combinacoes: '#{id} · {nome}' onde nome ja traz os dois sabores."
        ),
    }


def _format_pending_rows(
    items: list[dict], *, value_field: str, label_fn
) -> dict[str, Any]:
    rows = []
    for item in items:
        if not isinstance(item, dict):
            continue
        label = label_fn(item)
        rows.append(
            {
                "nome": label,
                "pedidos_pendentes": item.get(value_field)
                or item.get("quantidade")
                or item.get("qtd"),
            }
        )
    return {
        "itens": rows,
        "instruction": (
            _PRESENT_LIST_INSTRUCTION
            + " Uma linha por item: 'Nome · N pedido(s) pendente(s)'."
        ),
    }


def _extract_list(payload: Any) -> list:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return data
    return []


def _enrich_next_batch(result: Any) -> Any:
    if not isinstance(result, dict):
        return result
    if result.get("data") is None and "message" in result:
        return result
    if "fornada" in result:
        return result
    if result.get("id") is None:
        return result
    summary = build_batch_summary(result)
    return {
        "fornada": summary,
        "instruction": next_batch_instruction(),
    }


def _enrich_active_batches_list(result: dict) -> dict:
    items = result.get("data")
    if not isinstance(items, list):
        return result
    rows = []
    for item in items:
        if not isinstance(item, dict):
            continue
        summary = build_batch_summary(item)
        rows.append(summary)
    return {
        "fornadas": rows,
        "total": len(rows),
        "instruction": (
            "Cada fornada esta aberta no sistema (nao encerrada). "
            "Para cada uma, diga periodo e periodo_calendario_texto. "
            "Nao use 'em andamento' como sinonimo de ativo=true."
        ),
    }


def _enrich_active_batch_with_products(result: dict) -> dict:
    fa = result.get("fornada_ativa")
    if isinstance(fa, dict) and fa.get("status_sistema_texto"):
        out = dict(result)
        out["instruction"] = active_batch_instruction()
        return out
    return result


def _ensure_order_lines(rows: list) -> list[dict]:
    out: list[dict] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        row = simplify_order_row(item) if "linha" not in item else dict(item)
        if "linha" not in row:
            row["linha"] = format_order_line(row)
        out.append(row)
    return out


def _enrich_trimmed_orders_payload(result: dict) -> dict:
    if not isinstance(result.get("data"), list):
        return result
    out = dict(result)
    out["data"] = _ensure_order_lines(out["data"])
    out["instruction"] = _PRESENT_ORDERS_INSTRUCTION
    return out


def prepare_tool_result_for_llm(tool_name: str, result: Any) -> Any:
    if isinstance(result, dict) and result.get("error"):
        return result

    if tool_name == "get_recent_orders":
        return trim_orders_for_llm(_extract_list(result), limit=DEFAULT_ORDER_LIMIT)

    if tool_name in (
        "get_orders_by_status",
        "get_orders_by_delivery_date",
        "get_orders_by_dough",
        "get_orders_by_filling",
    ):
        if isinstance(result, dict) and isinstance(result.get("data"), list):
            return _enrich_trimmed_orders_payload(result)
        return trim_orders_for_llm(_extract_list(result), limit=DEFAULT_ORDER_LIMIT)

    if tool_name == "get_upcoming_deliveries":
        return trim_orders_for_llm(_extract_list(result), limit=DEFAULT_ORDER_LIMIT)

    if tool_name == "get_doughs_catalog":
        return _format_doughs(_extract_list(result))

    if tool_name == "get_fillings_catalog":
        return _format_unitario_fillings(_extract_list(result))

    if tool_name == "get_exclusive_fillings_catalog":
        return _format_exclusive_fillings(_extract_list(result))

    if tool_name == "get_pending_doughs":
        items = _extract_list(result)
        return _format_pending_rows(
            items,
            value_field="quantidade",
            label_fn=lambda i: massa_display_label(
                {
                    "sabor": i.get("nomeMassa")
                    or i.get("massa")
                    or i.get("sabor"),
                    **i,
                }
            ),
        )

    if tool_name == "get_pending_fillings":
        items = _extract_list(result)
        return _format_pending_rows(
            items,
            value_field="quantidade",
            label_fn=lambda i: format_display_token(
                str(
                    i.get("nomeRecheio")
                    or i.get("recheio")
                    or i.get("nome")
                    or ""
                )
            ),
        )

    if tool_name == "get_next_batch":
        return _enrich_next_batch(result)

    if tool_name == "get_active_batches":
        if isinstance(result, dict):
            return _enrich_active_batches_list(result)
        return result

    if tool_name == "get_active_batch_with_products":
        if isinstance(result, dict):
            return _enrich_active_batch_with_products(result)
        return result

    if tool_name == "generate_whatsapp_message":
        if isinstance(result, dict) and result.get("message_text"):
            return {
                "message_text": result["message_text"],
                "order_ids": result.get("order_ids"),
                "instruction": (
                    "Responda com o texto de message_text apenas — pronto para "
                    "copiar no WhatsApp. Sem aspas extras, sem 'Segue o texto' "
                    "nem explicacao antes ou depois."
                ),
            }
        return result

    if isinstance(result, dict) and result.get("truncated") is not None:
        return _enrich_trimmed_orders_payload(result)

    return result
