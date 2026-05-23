"""Tools de detalhamento de pedidos.

Mapeiam endpoints do ResumoPedidoController que ate entao nao eram usados pelo
assistente, permitindo respostas mais ricas (massa, recheio, formato, tamanho,
observacoes), filtros por data de entrega, status, massa e recheio.

O usuario cita o *resumo* (Pedido #3014 no Kanban). Esta tool resolve
`GET /resumo-pedido/{id}` e chama `.../pedido-bolo/detalhe/{pedidoBoloId}`.
O JSON de detalhe traz `numeroPedido` = id interno do pedido de bolo (ex. 221);
a resposta enriquecida inclui `pedido_numero` = id do resumo (#3014 no app).
"""

import google.genai as genai
import httpx

from app.core.llm_present import trim_orders_for_llm as _trim_orders_for_llm
from app.tools.order_filters import (
    fetch_orders_by_delivery_date as _fetch_orders_by_delivery_date,
    fetch_orders_by_mass_ids as _fetch_orders_by_mass_ids,
    resolve_mass_ids_for_name as _resolve_mass_ids_for_name,
)
from app.tools.order_ref import parse_resumo_order_id

_VALID_STATUS = {"PENDENTE", "PAGO", "CONCLUIDO", "CANCELADO"}


def _enrich_cake_detail_for_llm(detail: dict, pedido_numero_resumo: int) -> dict:
    """Evita confundir numeroPedido (id interno) com o numero visivel no app."""
    internal = detail.get("numeroPedido")
    enriched = dict(detail)
    enriched["pedido_numero"] = pedido_numero_resumo
    if internal is not None:
        enriched["pedido_bolo_id_interno"] = internal
    enriched["instruction"] = (
        f"O numero do pedido para o usuario e Pedido #{pedido_numero_resumo} "
        "(Kanban/app/WhatsApp). O campo numeroPedido na API e id interno do "
        "pedido de bolo — nao cite esse valor como numero do pedido."
    )
    return enriched


def _api_error_message(resp: httpx.Response, default: str) -> str:
    try:
        data = resp.json()
        if isinstance(data, dict):
            detail = data.get("detail")
            if isinstance(detail, str) and detail.strip():
                return detail.strip()
            if isinstance(detail, list) and detail:
                first = detail[0]
                if isinstance(first, dict):
                    msg = first.get("msg") or first.get("message")
                    if msg:
                        return str(msg)
    except Exception:
        pass
    raw = getattr(resp, "text", None)
    if isinstance(raw, str):
        stripped = raw.strip()
        if stripped:
            return stripped[:500]
    return default


DECLARATIONS = [
    genai.types.FunctionDeclaration(
        name="get_order_summary_by_id",
        description=(
            "Retorna o resumo agregado de um pedido (cliente, status, valor, "
            "data de entrega) a partir do ID. Use quando o usuario pedir "
            "informacoes basicas de um pedido especifico."
        ),
        parameters=genai.types.Schema(
            type=genai.types.Type.OBJECT,
            properties={
                "order_id": genai.types.Schema(
                    type=genai.types.Type.INTEGER,
                    description=(
                        "Numero do pedido como no app (Pedido #42) ou no WhatsApp — "
                        "e o id do resumo. Pode ser o inteiro 42."
                    ),
                ),
            },
            required=["order_id"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="get_cake_order_details",
        description=(
            "Retorna os detalhes completos de montagem de um pedido de bolo "
            "(massa, recheio, cobertura, decoracao, formato, tamanho, "
            "observacoes). O numero visivel (Pedido #X / Kanban / WhatsApp) e o "
            "id do RESUMO — use em order_id (inteiro X)."
        ),
        parameters=genai.types.Schema(
            type=genai.types.Type.OBJECT,
            properties={
                "order_id": genai.types.Schema(
                    type=genai.types.Type.INTEGER,
                    description=(
                        "Numero do pedido (resumo): o mesmo do app Pedido #X. "
                        "A tool resolve para o pedido de bolo interno."
                    ),
                ),
            },
            required=["order_id"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="get_batch_order_details",
        description=(
            "Retorna os detalhes de um pedido de fornada (produto, quantidade, "
            "fornada associada, etc.). O numero visivel (Pedido #X) e o id do "
            "RESUMO — use em order_id."
        ),
        parameters=genai.types.Schema(
            type=genai.types.Type.OBJECT,
            properties={
                "order_id": genai.types.Schema(
                    type=genai.types.Type.INTEGER,
                    description=(
                        "Numero do pedido (resumo), mesmo do app. "
                        "A tool resolve para o pedido de fornada interno."
                    ),
                ),
            },
            required=["order_id"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="get_orders_by_status",
        description=(
            "Lista resumos de pedidos filtrados por status. Util para responder "
            "'quais pedidos estao PENDENTES?', 'me mostra os PAGOS', etc."
        ),
        parameters=genai.types.Schema(
            type=genai.types.Type.OBJECT,
            properties={
                "status": genai.types.Schema(
                    type=genai.types.Type.STRING,
                    enum=["PENDENTE", "PAGO", "CONCLUIDO", "CANCELADO"],
                    description="Status do pedido.",
                ),
            },
            required=["status"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="get_orders_by_delivery_date",
        description=(
            "Lista pedidos de bolo cuja data de entrega e a informada (formato "
            "ISO YYYY-MM-DD). Opcionalmente filtra por status. Util para "
            "perguntas como 'quais pedidos sao para amanha?' ou 'o que tem "
            "para entregar dia 12?'."
        ),
        parameters=genai.types.Schema(
            type=genai.types.Type.OBJECT,
            properties={
                "delivery_date": genai.types.Schema(
                    type=genai.types.Type.STRING,
                    description=(
                        "Data de entrega: dd/MM/yyyy (ex.: 10/01/2025) ou yyyy-MM-dd."
                    ),
                ),
                "status": genai.types.Schema(
                    type=genai.types.Type.STRING,
                    enum=["PENDENTE", "PAGO", "CONCLUIDO", "CANCELADO"],
                    description="Status para filtrar (opcional).",
                ),
            },
            required=["delivery_date"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="get_orders_by_dough",
        description=(
            "Lista pedidos de bolo por massa. Informe dough_id OU massa_nome "
            "(ex.: cacau, chocolate). Inclui todos os ids de cadastro com o "
            "mesmo sabor. Ate 8 pedidos mais recentes no retorno."
        ),
        parameters=genai.types.Schema(
            type=genai.types.Type.OBJECT,
            properties={
                "dough_id": genai.types.Schema(
                    type=genai.types.Type.INTEGER,
                    description="ID da massa (opcional se massa_nome for informado).",
                ),
                "massa_nome": genai.types.Schema(
                    type=genai.types.Type.STRING,
                    description=(
                        "Nome/sabor da massa (ex.: cacau). Preferivel quando o "
                        "usuario citar o sabor em vez do id."
                    ),
                ),
                "status": genai.types.Schema(
                    type=genai.types.Type.STRING,
                    enum=["PENDENTE", "PAGO", "CONCLUIDO", "CANCELADO"],
                    description="Status para filtrar (opcional).",
                ),
            },
        ),
    ),
    genai.types.FunctionDeclaration(
        name="get_orders_by_filling",
        description=(
            "Lista pedidos de bolo que usam um recheio composto especifico "
            "(pelo ID do recheio_pedido). Retorna ate os 10 mais recentes "
            "(campo total = quantidade no sistema). Filtra opcionalmente por status."
        ),
        parameters=genai.types.Schema(
            type=genai.types.Type.OBJECT,
            properties={
                "filling_id": genai.types.Schema(
                    type=genai.types.Type.INTEGER,
                    description="ID do recheio composto (recheio_pedido).",
                ),
                "status": genai.types.Schema(
                    type=genai.types.Type.STRING,
                    enum=["PENDENTE", "PAGO", "CONCLUIDO", "CANCELADO"],
                    description="Status para filtrar (opcional).",
                ),
            },
            required=["filling_id"],
        ),
    ),
]


async def execute(
    name: str, args: dict, base_url: str, token: str | None, client: httpx.AsyncClient
) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    if name == "get_order_summary_by_id":
        raw_oid = args.get("order_id")
        if raw_oid is None:
            return {"error": "order_id e obrigatorio."}
        try:
            order_id = parse_resumo_order_id(raw_oid)
        except ValueError as exc:
            return {"error": str(exc)}
        url = f"{base_url}/resumo-pedido/{order_id}"
        resp = await client.get(url, headers=headers)
        if resp.status_code == 404:
            return {"error": f"Pedido {order_id} nao encontrado."}
        resp.raise_for_status()
        return resp.json()

    if name == "get_cake_order_details":
        raw_oid = args.get("order_id")
        if raw_oid is None:
            return {"error": "order_id e obrigatorio."}
        try:
            order_id = parse_resumo_order_id(raw_oid)
        except ValueError as exc:
            return {"error": str(exc)}

        resumo_url = f"{base_url}/resumo-pedido/{order_id}"
        resumo_resp = await client.get(resumo_url, headers=headers)
        detail_bolo_id: int | None = None

        if resumo_resp.status_code == 200:
            resumo_body = resumo_resp.json()
            pedido_bolo_id = resumo_body.get("pedidoBoloId")
            if pedido_bolo_id is None:
                return {
                    "error": (
                        f"O resumo de pedido #{order_id} nao e um pedido de bolo "
                        "(nao ha bolo vinculado). Se for fornada, peca os detalhes "
                        "de pedido de fornada."
                    )
                }
            detail_bolo_id = int(pedido_bolo_id)
        elif resumo_resp.status_code == 404:
            return {
                "error": (
                    f"Pedido #{order_id} nao encontrado. Use o numero do Kanban "
                    "(resumo), nao o id interno do pedido de bolo."
                )
            }
        else:
            return {
                "error": _api_error_message(
                    resumo_resp,
                    f"Nao foi possivel carregar o resumo #{order_id}.",
                )
            }

        detail_url = f"{base_url}/resumo-pedido/pedido-bolo/detalhe/{detail_bolo_id}"
        resp = await client.get(detail_url, headers=headers)
        if resp.status_code == 404:
            msg = _api_error_message(resp, "")
            return {
                "error": (
                    msg
                    or (
                        f"Nao encontramos o pedido de bolo vinculado ao resumo #{order_id}. "
                        "Confira o numero no Kanban ou se o pedido ainda esta ativo."
                    )
                )
            }
        if resp.status_code >= 400:
            return {
                "error": _api_error_message(
                    resp,
                    f"Erro ao buscar detalhes do bolo (HTTP {resp.status_code}).",
                )
            }
        body = resp.json()
        if isinstance(body, dict):
            return _enrich_cake_detail_for_llm(body, order_id)
        return body

    if name == "get_batch_order_details":
        raw_oid = args.get("order_id")
        if raw_oid is None:
            return {"error": "order_id e obrigatorio."}
        try:
            order_id = parse_resumo_order_id(raw_oid)
        except ValueError as exc:
            return {"error": str(exc)}

        resumo_url = f"{base_url}/resumo-pedido/{order_id}"
        resumo_resp = await client.get(resumo_url, headers=headers)
        detail_fornada_id: int | None = None

        if resumo_resp.status_code == 200:
            resumo_body = resumo_resp.json()
            pedido_fornada_id = resumo_body.get("pedidoFornadaId")
            if pedido_fornada_id is None:
                return {
                    "error": (
                        f"O resumo de pedido #{order_id} nao e um pedido de fornada. "
                        "Use os detalhes de pedido de bolo se for um bolo."
                    )
                }
            detail_fornada_id = int(pedido_fornada_id)
        elif resumo_resp.status_code == 404:
            detail_fornada_id = int(order_id)
        else:
            return {
                "error": _api_error_message(
                    resumo_resp,
                    f"Nao foi possivel carregar o resumo #{order_id}.",
                )
            }

        detail_url = f"{base_url}/resumo-pedido/pedido-fornada/detalhe/{detail_fornada_id}"
        resp = await client.get(detail_url, headers=headers)
        if resp.status_code == 404:
            msg = _api_error_message(resp, "")
            return {
                "error": (
                    msg
                    or (
                        f"Nao encontramos o pedido de fornada vinculado ao resumo #{order_id}. "
                        "Confira o numero no Kanban ou se o pedido ainda esta ativo."
                    )
                )
            }
        if resp.status_code >= 400:
            return {
                "error": _api_error_message(
                    resp,
                    f"Erro ao buscar detalhes da fornada (HTTP {resp.status_code}).",
                )
            }
        return resp.json()

    if name == "get_orders_by_status":
        status = (args.get("status") or "").upper()
        if status not in _VALID_STATUS:
            return {"error": f"status invalido. Use um de {sorted(_VALID_STATUS)}."}
        url = f"{base_url}/resumo-pedido/status/{status}"
        resp = await client.get(url, headers=headers)
        if resp.status_code == 204:
            return {"data": [], "message": f"Nenhum pedido com status {status}."}
        resp.raise_for_status()
        raw = resp.json()
        if not isinstance(raw, list):
            raw = []
        return _trim_orders_for_llm(raw)

    if name == "get_orders_by_delivery_date":
        delivery_date = args.get("delivery_date")
        status_filter = (args.get("status") or "").upper() or None
        if status_filter and status_filter not in _VALID_STATUS:
            return {"error": f"status invalido. Use um de {sorted(_VALID_STATUS)}."}
        return await _fetch_orders_by_delivery_date(
            client, base_url, token, delivery_date, status_filter
        )

    if name == "get_orders_by_dough":
        dough_id = args.get("dough_id")
        massa_nome = args.get("massa_nome")
        status_filter = (args.get("status") or "").upper() or None
        if status_filter and status_filter not in _VALID_STATUS:
            return {"error": f"status invalido. Use um de {sorted(_VALID_STATUS)}."}
        massa_ids: list[int] = []
        if dough_id is not None:
            try:
                massa_ids = [int(dough_id)]
            except (TypeError, ValueError):
                return {"error": "dough_id invalido."}
        elif isinstance(massa_nome, str) and massa_nome.strip():
            massa_ids, err = await _resolve_mass_ids_for_name(
                client, base_url, token, massa_nome.strip()
            )
            if err:
                return err
        else:
            return {"error": "Informe dough_id ou massa_nome."}
        result = await _fetch_orders_by_mass_ids(
            client, base_url, token, massa_ids, status_filter
        )
        if not result.get("data") and not result.get("error"):
            result["message"] = (
                "Nenhum pedido de bolo encontrado para essa massa no cadastro atual."
            )
        return result

    if name == "get_orders_by_filling":
        filling_id = args.get("filling_id")
        if filling_id is None:
            return {"error": "filling_id e obrigatorio."}
        params = {}
        status_filter = (args.get("status") or "").upper()
        if status_filter:
            if status_filter not in _VALID_STATUS:
                return {"error": f"status invalido. Use um de {sorted(_VALID_STATUS)}."}
            params["status"] = status_filter
        url = f"{base_url}/resumo-pedido/pedido-bolo/por-recheio/{filling_id}"
        resp = await client.get(url, params=params, headers=headers)
        if resp.status_code == 204:
            return {"data": [], "message": "Nenhum pedido encontrado para esse recheio."}
        resp.raise_for_status()
        raw = resp.json()
        if not isinstance(raw, list):
            raw = []
        return _trim_orders_for_llm(raw)

    return {"error": f"Tool desconhecida: {name}"}
