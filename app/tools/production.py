import google.genai as genai
import httpx

DECLARATIONS = [
    genai.types.FunctionDeclaration(
        name="get_pending_doughs",
        description=(
            "Retorna as massas que possuem pedidos pendentes ou pagos aguardando producao, "
            "com a quantidade de pedidos para cada massa."
        ),
        parameters=genai.types.Schema(type=genai.types.Type.OBJECT, properties={}),
    ),
    genai.types.FunctionDeclaration(
        name="get_pending_fillings",
        description=(
            "Retorna os recheios que possuem pedidos pendentes ou pagos aguardando producao, "
            "com a quantidade de pedidos para cada recheio."
        ),
        parameters=genai.types.Schema(type=genai.types.Type.OBJECT, properties={}),
    ),
    genai.types.FunctionDeclaration(
        name="get_upcoming_deliveries",
        description=(
            "Retorna pedidos com data de entrega proxima (nos proximos dias), "
            "incluindo dados do cliente, valor, status e tipo de entrega. "
            "Util para planejamento de producao e logistica."
        ),
        parameters=genai.types.Schema(
            type=genai.types.Type.OBJECT,
            properties={
                "days_ahead": genai.types.Schema(
                    type=genai.types.Type.INTEGER,
                    description="Numero de dias a frente para buscar (padrao: 7)",
                ),
            },
        ),
    ),
]

ENDPOINTS = {
    "get_pending_doughs": "/dashboard/massas-pendentes",
    "get_pending_fillings": "/dashboard/recheios-pendentes",
}


async def execute(
    name: str, args: dict, base_url: str, token: str | None, client: httpx.AsyncClient
) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    if name == "get_upcoming_deliveries":
        days = args.get("days_ahead", 7)
        url = f"{base_url}/dashboard/pedidos-proximos-entrega"
        params = {"diasProximos": days}
    elif name in ENDPOINTS:
        url = f"{base_url}{ENDPOINTS[name]}"
        params = {}
    else:
        return {"error": f"Tool desconhecida: {name}"}

    resp = await client.get(url, params=params, headers=headers)
    if resp.status_code == 204:
        return {"data": [], "message": "Nenhum dado encontrado"}
    resp.raise_for_status()
    return resp.json()
