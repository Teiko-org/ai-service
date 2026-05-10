"""Tools de detalhamento de pedidos.

Mapeiam endpoints do ResumoPedidoController que ate entao nao eram usados pelo
assistente, permitindo respostas mais ricas (massa, recheio, formato, tamanho,
observacoes), filtros por data de entrega, status, massa e recheio.

O endpoint Java `.../pedido-bolo/detalhe/{id}` espera o ID interno do *pedido de
bolo*. O usuario costuma citar o *resumo* (mesmo numero do Kanban). Por isso
esta tool primeiro consulta `GET /resumo-pedido/{id}` e usa `pedidoBoloId` quando
existir; se o resumo nao existir, tenta o detalhe com o proprio numero (fallback).
"""

import google.genai as genai
import httpx

_VALID_STATUS = {"PENDENTE", "PAGO", "CONCLUIDO", "CANCELADO"}


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
                    description="ID do resumo de pedido.",
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
            "observacoes). O numero que o usuario ve no Kanban e o ID do "
            "RESUMO de pedido — use esse valor em order_id."
        ),
        parameters=genai.types.Schema(
            type=genai.types.Type.OBJECT,
            properties={
                "order_id": genai.types.Schema(
                    type=genai.types.Type.INTEGER,
                    description=(
                        "ID do resumo de pedido (ex.: numero no Kanban). "
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
            "fornada associada, etc.). O numero visivel no Kanban e o ID do "
            "RESUMO — use em order_id."
        ),
        parameters=genai.types.Schema(
            type=genai.types.Type.OBJECT,
            properties={
                "order_id": genai.types.Schema(
                    type=genai.types.Type.INTEGER,
                    description=(
                        "ID do resumo de pedido. A tool resolve para o "
                        "pedido de fornada interno."
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
                    description="Data de entrega no formato YYYY-MM-DD.",
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
            "Lista pedidos de bolo que usam uma massa especifica (pelo ID da "
            "massa). Util para 'quais pedidos usam massa de chocolate?'. "
            "Antes, descubra o ID com a tool de catalogo se o usuario citar "
            "o nome."
        ),
        parameters=genai.types.Schema(
            type=genai.types.Type.OBJECT,
            properties={
                "dough_id": genai.types.Schema(
                    type=genai.types.Type.INTEGER,
                    description="ID da massa.",
                ),
                "status": genai.types.Schema(
                    type=genai.types.Type.STRING,
                    enum=["PENDENTE", "PAGO", "CONCLUIDO", "CANCELADO"],
                    description="Status para filtrar (opcional).",
                ),
            },
            required=["dough_id"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="get_orders_by_filling",
        description=(
            "Lista pedidos de bolo que usam um recheio composto especifico "
            "(pelo ID do recheio_pedido). Filtra opcionalmente por status."
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
        order_id = args.get("order_id")
        if order_id is None:
            return {"error": "order_id e obrigatorio."}
        url = f"{base_url}/resumo-pedido/{order_id}"
        resp = await client.get(url, headers=headers)
        if resp.status_code == 404:
            return {"error": f"Pedido {order_id} nao encontrado."}
        resp.raise_for_status()
        return resp.json()

    if name == "get_cake_order_details":
        order_id = args.get("order_id")
        if order_id is None:
            return {"error": "order_id e obrigatorio."}

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
            detail_bolo_id = int(order_id)
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
        return resp.json()

    if name == "get_batch_order_details":
        order_id = args.get("order_id")
        if order_id is None:
            return {"error": "order_id e obrigatorio."}

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
        return {"data": resp.json()}

    if name == "get_orders_by_delivery_date":
        delivery_date = args.get("delivery_date")
        if not delivery_date:
            return {"error": "delivery_date e obrigatorio (YYYY-MM-DD)."}
        params: dict = {"dataEntrega": delivery_date}
        status_filter = (args.get("status") or "").upper()
        if status_filter:
            if status_filter not in _VALID_STATUS:
                return {"error": f"status invalido. Use um de {sorted(_VALID_STATUS)}."}
            params["status"] = status_filter
        url = f"{base_url}/resumo-pedido/pedido-bolo/por-data-entrega"
        resp = await client.get(url, params=params, headers=headers)
        if resp.status_code == 204:
            return {
                "data": [],
                "message": f"Nenhum pedido para entrega em {delivery_date}.",
            }
        resp.raise_for_status()
        return {"data": resp.json()}

    if name == "get_orders_by_dough":
        dough_id = args.get("dough_id")
        if dough_id is None:
            return {"error": "dough_id e obrigatorio."}
        params = {}
        status_filter = (args.get("status") or "").upper()
        if status_filter:
            if status_filter not in _VALID_STATUS:
                return {"error": f"status invalido. Use um de {sorted(_VALID_STATUS)}."}
            params["status"] = status_filter
        url = f"{base_url}/resumo-pedido/pedido-bolo/por-massa/{dough_id}"
        resp = await client.get(url, params=params, headers=headers)
        if resp.status_code == 204:
            return {"data": [], "message": "Nenhum pedido encontrado para essa massa."}
        resp.raise_for_status()
        return {"data": resp.json()}

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
        return {"data": resp.json()}

    return {"error": f"Tool desconhecida: {name}"}
