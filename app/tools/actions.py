"""Tools de acao (Agentic AI) — alteram o estado do sistema via PATCH/POST.

Padrao two-step para garantir confirmacao humana antes de qualquer escrita:

  1. Primeira chamada com `confirmed=False` (default): a tool NAO chama o backend.
     Retorna `requires_confirmation: True` + preview do pedido. O modelo deve
     mostrar a previa e perguntar "Deseja confirmar?".
  2. Segunda chamada com `confirmed=True`: a tool executa de fato a operacao
     no backend.

Esse padrao e citado pelo SYSTEM_PROMPT, que orienta o Gemini a NUNCA chamar
uma acao com `confirmed=True` sem antes ter pedido confirmacao explicita ao
usuario na conversa.
"""

import logging
from typing import Any

import google.genai as genai
import httpx

logger = logging.getLogger(__name__)

ACTION_TOOL_NAMES = {
    "mark_order_as_paid",
    "mark_order_as_completed",
    "mark_order_as_cancelled",
    "mark_order_as_pending",
    "generate_whatsapp_message",
}

# Mapeia status -> sufixo do endpoint PATCH
_STATUS_TRANSITIONS = {
    "mark_order_as_paid": ("PAGO", "pago"),
    "mark_order_as_completed": ("CONCLUIDO", "concluido"),
    "mark_order_as_cancelled": ("CANCELADO", "cancelado"),
    "mark_order_as_pending": ("PENDENTE", "pendente"),
}


def _confirmed_param() -> genai.types.Schema:
    return genai.types.Schema(
        type=genai.types.Type.BOOLEAN,
        description=(
            "False (padrao) retorna apenas a previa para o usuario confirmar; "
            "True executa a acao de fato. NUNCA passe True sem antes ter "
            "perguntado e recebido confirmacao explicita do usuario."
        ),
    )


def _order_id_param() -> genai.types.Schema:
    return genai.types.Schema(
        type=genai.types.Type.INTEGER,
        description="ID do resumo de pedido (visivel no Kanban / lista de pedidos).",
    )


DECLARATIONS = [
    genai.types.FunctionDeclaration(
        name="mark_order_as_paid",
        description=(
            "Marca um resumo de pedido como PAGO. Acao destrutiva: SEMPRE "
            "chame primeiro com confirmed=False, mostre a previa ao usuario "
            "e so chame com confirmed=True apos confirmacao explicita."
        ),
        parameters=genai.types.Schema(
            type=genai.types.Type.OBJECT,
            properties={"order_id": _order_id_param(), "confirmed": _confirmed_param()},
            required=["order_id"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="mark_order_as_completed",
        description=(
            "Marca um resumo de pedido como CONCLUIDO. Acao destrutiva: "
            "SEMPRE pedir confirmacao antes (mesmo padrao de mark_order_as_paid)."
        ),
        parameters=genai.types.Schema(
            type=genai.types.Type.OBJECT,
            properties={"order_id": _order_id_param(), "confirmed": _confirmed_param()},
            required=["order_id"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="mark_order_as_cancelled",
        description=(
            "Cancela um resumo de pedido (status CANCELADO). Acao destrutiva: "
            "SEMPRE pedir confirmacao antes (mesmo padrao de mark_order_as_paid)."
        ),
        parameters=genai.types.Schema(
            type=genai.types.Type.OBJECT,
            properties={"order_id": _order_id_param(), "confirmed": _confirmed_param()},
            required=["order_id"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="mark_order_as_pending",
        description=(
            "Volta um resumo de pedido para PENDENTE. Acao destrutiva: SEMPRE "
            "pedir confirmacao antes."
        ),
        parameters=genai.types.Schema(
            type=genai.types.Type.OBJECT,
            properties={"order_id": _order_id_param(), "confirmed": _confirmed_param()},
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
                    description="IDs dos resumos de pedido a consolidar na mensagem.",
                ),
            },
            required=["order_ids"],
        ),
    ),
]


async def _fetch_order_preview(
    client: httpx.AsyncClient, base_url: str, headers: dict, order_id: int
) -> dict[str, Any]:
    """Le o resumo do pedido atual para mostrar previa antes da acao."""
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
    order_id = args.get("order_id")
    confirmed = bool(args.get("confirmed", False))
    status_label, endpoint_suffix = _STATUS_TRANSITIONS[name]

    if order_id is None:
        return {"error": "order_id e obrigatorio."}

    if not confirmed:
        preview = await _fetch_order_preview(client, base_url, headers, order_id)
        if "error" in preview:
            return preview
        return {
            "requires_confirmation": True,
            "action": name,
            "order_id": order_id,
            "target_status": status_label,
            "message": (
                f"Vou alterar o pedido #{order_id} para {status_label}. "
                "Confirme com o usuario antes de executar."
            ),
            **preview,
        }

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
    raw_ids = args.get("order_ids") or []
    try:
        ids = [int(x) for x in raw_ids]
    except (TypeError, ValueError):
        return {"error": "order_ids deve ser uma lista de inteiros."}

    if not ids:
        return {"error": "Informe pelo menos um order_id."}

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
