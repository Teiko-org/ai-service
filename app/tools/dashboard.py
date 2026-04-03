import google.genai as genai
import httpx

DECLARATIONS = [
    genai.types.FunctionDeclaration(
        name="get_unique_clients_count",
        description="Retorna o total de clientes unicos que fizeram pedidos na confeitaria.",
        parameters=genai.types.Schema(type=genai.types.Type.OBJECT, properties={}),
    ),
    genai.types.FunctionDeclaration(
        name="get_orders_count",
        description=(
            "Retorna a contagem total de pedidos agrupados por status "
            "(PENDENTE, PAGO, CONCLUIDO, CANCELADO). Inclui bolos e fornadas."
        ),
        parameters=genai.types.Schema(type=genai.types.Type.OBJECT, properties={}),
    ),
    genai.types.FunctionDeclaration(
        name="get_cake_orders_count",
        description="Retorna a contagem de pedidos de bolo agrupados por status.",
        parameters=genai.types.Schema(type=genai.types.Type.OBJECT, properties={}),
    ),
    genai.types.FunctionDeclaration(
        name="get_batch_orders_count",
        description="Retorna a contagem de pedidos de fornada agrupados por status.",
        parameters=genai.types.Schema(type=genai.types.Type.OBJECT, properties={}),
    ),
    genai.types.FunctionDeclaration(
        name="get_top_products",
        description=(
            "Retorna o ranking dos produtos mais pedidos da confeitaria, "
            "incluindo bolos e produtos de fornada, com nome, quantidade e valor total."
        ),
        parameters=genai.types.Schema(type=genai.types.Type.OBJECT, properties={}),
    ),
    genai.types.FunctionDeclaration(
        name="get_top_cakes",
        description="Retorna o ranking dos bolos mais pedidos com detalhes de montagem.",
        parameters=genai.types.Schema(type=genai.types.Type.OBJECT, properties={}),
    ),
    genai.types.FunctionDeclaration(
        name="get_top_batch_products",
        description="Retorna o ranking dos produtos de fornada mais pedidos.",
        parameters=genai.types.Schema(type=genai.types.Type.OBJECT, properties={}),
    ),
    genai.types.FunctionDeclaration(
        name="get_recent_orders",
        description=(
            "Retorna os 50 pedidos mais recentes com dados do cliente, "
            "status, valor e tipo de entrega."
        ),
        parameters=genai.types.Schema(type=genai.types.Type.OBJECT, properties={}),
    ),
]

ENDPOINTS = {
    "get_unique_clients_count": "/dashboard/qtdClientesUnicos",
    "get_orders_count": "/dashboard/qtdPedidos",
    "get_cake_orders_count": "/dashboard/qtdPedidosBolo",
    "get_batch_orders_count": "/dashboard/qtdPedidosFornada",
    "get_top_products": "/dashboard/produtosMaisPedidos",
    "get_top_cakes": "/dashboard/bolosMaisPedidos",
    "get_top_batch_products": "/dashboard/produtosFornadasMaisPedidos",
    "get_recent_orders": "/dashboard/ultimosPedidos",
}


async def execute(
    name: str, args: dict, base_url: str, token: str | None, client: httpx.AsyncClient
) -> dict:
    url = f"{base_url}{ENDPOINTS[name]}"
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    resp = await client.get(url, headers=headers)
    if resp.status_code == 204:
        return {"data": [], "message": "Nenhum dado encontrado"}
    resp.raise_for_status()
    return resp.json()
