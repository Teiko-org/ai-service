"""Tools de acao (Agentic AI) — alteram o estado do sistema via PATCH/POST.

Fluxo de confirmacao (uma unica previa):

  1. Chamada com `confirmed=False`: previa + `confirm_token` (HMAC) quando
     CONFIRM_TOKEN_SECRET esta configurado; o app mostra botao Confirmar.
  2. Commit via botao (`confirmation` no /ask) ou texto "sim" (interceptado
     no servidor) ou segunda chamada da tool com `confirmed=True` + token.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import google.genai as genai
import httpx

from app.config import settings
from app.core.confirm_tokens import ConfirmTokenError
from app.tools.order_ref import parse_resumo_order_id, parse_resumo_order_id_list
from app.tools.writes._helpers import WriteToolError, issue_preview, preview_response, verify_commit

logger = logging.getLogger(__name__)

ACTION_TOOL_NAMES = {
    "mark_order_as_paid",
    "mark_order_as_completed",
    "mark_order_as_cancelled",
    "mark_order_as_pending",
    "generate_whatsapp_message",
}

_STATUS_TRANSITIONS = {
    "mark_order_as_paid": ("PAGO", "pago"),
    "mark_order_as_completed": ("CONCLUIDO", "concluido"),
    "mark_order_as_cancelled": ("CANCELADO", "cancelado"),
    "mark_order_as_pending": ("PENDENTE", "pendente"),
}

_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


def _format_brl(value: float | int) -> str:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return str(value)
    formatted = f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {formatted}"


def _format_date_br(raw: str) -> str:
    if not raw:
        return raw
    text = str(raw).strip()
    m = _ISO_DATE_RE.match(text[:10])
    if m:
        y, mo, d = m.groups()
        return f"{d}/{mo}/{y}"
    return text


def _preview_detail_suffix(preview: dict | None) -> str:
    if not preview:
        return ""
    bits: list[str] = []
    valor = preview.get("valor")
    if valor is not None:
        bits.append(_format_brl(valor))
    entrega = preview.get("dataEntrega") or preview.get("data_entrega")
    if entrega:
        bits.append(f"com data de entrega em {_format_date_br(str(entrega))}")
    if not bits:
        return ""
    return f" ({', '.join(bits)})"


def _preview_message(
    order_id: int, order_data: dict | None, status_label: str
) -> str:
    detail = _preview_detail_suffix(order_data)
    return f"Vou marcar o pedido #{order_id}{detail} como {status_label}. Confirma?"


def _confirmed_param() -> genai.types.Schema:
    return genai.types.Schema(
        type=genai.types.Type.BOOLEAN,
        description=(
            "False (padrao) retorna apenas a previa; True executa apos confirmacao "
            "humana. Com CONFIRM_TOKEN_SECRET, o commit exige confirm_token da previa."
        ),
    )


def _confirm_token_param() -> genai.types.Schema:
    return genai.types.Schema(
        type=genai.types.Type.STRING,
        description=(
            "Token HMAC retornado na previa (confirm_token). Obrigatorio no commit "
            "quando o servidor emite tokens; repasse o valor sem alterar."
        ),
    )


def _order_id_param() -> genai.types.Schema:
    return genai.types.Schema(
        type=genai.types.Type.INTEGER,
        description=(
            "Numero do pedido: o mesmo do app (Pedido #X) e do WhatsApp "
            "(id do resumo de pedido)."
        ),
    )


def _status_tool_properties() -> dict:
    return {
        "order_id": _order_id_param(),
        "confirmed": _confirmed_param(),
        "confirm_token": _confirm_token_param(),
    }


DECLARATIONS = [
    genai.types.FunctionDeclaration(
        name="mark_order_as_paid",
        description=(
            "Marca um resumo de pedido como PAGO. Acao destrutiva: SEMPRE "
            "chame primeiro com confirmed=False, mostre a previa ao usuario "
            "e so commite apos confirmacao (botao ou sim + confirm_token)."
        ),
        parameters=genai.types.Schema(
            type=genai.types.Type.OBJECT,
            properties=_status_tool_properties(),
            required=["order_id"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="mark_order_as_completed",
        description=(
            "Marca um resumo de pedido como CONCLUIDO. Acao destrutiva: "
            "mesmo fluxo de confirmacao de mark_order_as_paid."
        ),
        parameters=genai.types.Schema(
            type=genai.types.Type.OBJECT,
            properties=_status_tool_properties(),
            required=["order_id"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="mark_order_as_cancelled",
        description=(
            "Cancela um resumo de pedido (status CANCELADO). Acao destrutiva: "
            "mesmo fluxo de confirmacao de mark_order_as_paid."
        ),
        parameters=genai.types.Schema(
            type=genai.types.Type.OBJECT,
            properties=_status_tool_properties(),
            required=["order_id"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="mark_order_as_pending",
        description=(
            "Volta um resumo de pedido para PENDENTE. Acao destrutiva: "
            "mesmo fluxo de confirmacao de mark_order_as_paid."
        ),
        parameters=genai.types.Schema(
            type=genai.types.Type.OBJECT,
            properties=_status_tool_properties(),
            required=["order_id"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="generate_whatsapp_message",
        description=(
            "Gera o texto consolidado de mensagem do WhatsApp para um ou mais "
            "resumos de pedido. NAO altera estado — pode ser chamada direto "
            "sem confirmacao previa. Retorna a string da mensagem ja "
            "formatada para envio."
        ),
        parameters=genai.types.Schema(
            type=genai.types.Type.OBJECT,
            properties={
                "order_ids": genai.types.Schema(
                    type=genai.types.Type.ARRAY,
                    items=genai.types.Schema(type=genai.types.Type.INTEGER),
                    description=(
                        "Numeros dos pedidos (ids do resumo), como Pedido #1 e #2 "
                        "no app — a consolidar na mensagem."
                    ),
                ),
            },
            required=["order_ids"],
        ),
    ),
]


async def _fetch_order_preview(
    client: httpx.AsyncClient, base_url: str, headers: dict, order_id: int
) -> dict[str, Any]:
    try:
        resp = await client.get(f"{base_url}/resumo-pedido/{order_id}", headers=headers)
        if resp.status_code == 404:
            return {"error": f"Pedido {order_id} nao encontrado."}
        resp.raise_for_status()
        return {"preview": resp.json()}
    except httpx.HTTPError as exc:
        logger.warning("Falha ao carregar previa do pedido %s: %s", order_id, exc)
        return {"preview": None, "warning": f"Nao foi possivel carregar previa: {exc}"}


async def _execute_status_transition(
    name: str,
    args: dict,
    base_url: str,
    headers: dict,
    client: httpx.AsyncClient,
) -> dict:
    raw_oid = args.get("order_id")
    confirmed = bool(args.get("confirmed", False))
    confirm_token = (args.get("confirm_token") or "").strip()
    status_label, endpoint_suffix = _STATUS_TRANSITIONS[name]
    canonical_args = {}

    if raw_oid is None:
        return {"error": "order_id e obrigatorio."}
    try:
        order_id = parse_resumo_order_id(raw_oid)
    except ValueError as exc:
        return {"error": str(exc)}
    canonical_args["order_id"] = order_id

    if not confirmed:
        preview = await _fetch_order_preview(client, base_url, headers, order_id)
        if "error" in preview:
            return preview
        order_data = preview.get("preview")
        if not isinstance(order_data, dict):
            order_data = None
        message = _preview_message(order_id, order_data, status_label)
        if settings.confirm_token_secret:
            try:
                token = issue_preview(name, canonical_args)
            except (ConfirmTokenError, WriteToolError) as exc:
                return {"error": str(exc)}
            return {
                **preview_response(name, token, canonical_args, message),
                "preview": preview.get("preview"),
                "target_status": status_label,
            }
        return {
            "requires_confirmation": True,
            "action": name,
            "order_id": order_id,
            "target_status": status_label,
            "message": message,
            "payload": canonical_args,
            **preview,
        }

    if settings.confirm_token_secret:
        if not confirm_token:
            return {
                "error": "Aguardando confirmacao do usuario antes de executar.",
                "code": "awaiting_confirmation",
            }
        try:
            verify_commit(name, canonical_args, confirm_token)
        except WriteToolError as exc:
            from app.core.confirmation_ux import humanize_confirm_error

            return {"error": humanize_confirm_error(str(exc))}

    url = f"{base_url}/resumo-pedido/{order_id}/{endpoint_suffix}"
    resp = await client.patch(url, headers=headers)
    if resp.status_code == 404:
        return {"error": f"Pedido {order_id} nao encontrado."}
    if resp.status_code == 422:
        return {
            "error": (
                f"Pedido {order_id} nao pode ser movido para {status_label} "
                "no estado atual."
            )
        }
    resp.raise_for_status()
    return {
        "ok": True,
        "action": name,
        "order_id": order_id,
        "new_status": status_label,
        "data": resp.json(),
    }


async def _generate_whatsapp_message(
    args: dict, base_url: str, headers: dict, client: httpx.AsyncClient
) -> dict:
    raw_ids = args.get("order_ids")
    try:
        ids = parse_resumo_order_id_list(raw_ids)
    except ValueError as exc:
        return {"error": str(exc)}

    url = f"{base_url}/resumo-pedido/mensagens"
    payload = {"idsResumo": ids}
    resp = await client.post(url, json=payload, headers=headers)
    if resp.status_code == 404:
        return {"error": "Um ou mais pedidos informados nao foram encontrados."}
    resp.raise_for_status()

    body = resp.text
    return {
        "ok": True,
        "order_ids": ids,
        "message_text": body,
    }


async def execute(
    name: str, args: dict, base_url: str, token: str | None, client: httpx.AsyncClient
) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    if name in _STATUS_TRANSITIONS:
        return await _execute_status_transition(name, args, base_url, headers, client)

    if name == "generate_whatsapp_message":
        return await _generate_whatsapp_message(args, base_url, headers, client)

    return {"error": f"Tool desconhecida: {name}"}
