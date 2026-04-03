import google.genai as genai
import httpx

DECLARATIONS = [
    genai.types.FunctionDeclaration(
        name="get_cake_orders_by_period",
        description=(
            "Retorna a quantidade de pedidos de bolo concluidos e cancelados "
            "agrupados por periodo. Util para analisar tendencias de vendas e sazonalidade."
        ),
        parameters=genai.types.Schema(
            type=genai.types.Type.OBJECT,
            properties={
                "period_type": genai.types.Schema(
                    type=genai.types.Type.STRING,
                    enum=["MES", "ANO"],
                    description="Agrupamento: MES (mensal) ou ANO (anual)",
                ),
                "year": genai.types.Schema(
                    type=genai.types.Type.INTEGER,
                    description="Ano para filtrar (ex: 2025)",
                ),
            },
            required=["period_type"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="get_batch_orders_by_period",
        description=(
            "Retorna a quantidade de pedidos de fornada concluidos e cancelados "
            "agrupados por periodo. Util para analisar desempenho de fornadas ao longo do tempo."
        ),
        parameters=genai.types.Schema(
            type=genai.types.Type.OBJECT,
            properties={
                "period_type": genai.types.Schema(
                    type=genai.types.Type.STRING,
                    enum=["MES", "ANO"],
                    description="Agrupamento: MES (mensal) ou ANO (anual)",
                ),
                "year": genai.types.Schema(
                    type=genai.types.Type.INTEGER,
                    description="Ano para filtrar (ex: 2025)",
                ),
            },
            required=["period_type"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="get_items_by_period",
        description=(
            "Retorna massas, recheios ou decoracoes mais pedidos agrupados por periodo. "
            "Util para identificar quais itens estao em alta ou em queda."
        ),
        parameters=genai.types.Schema(
            type=genai.types.Type.OBJECT,
            properties={
                "item_type": genai.types.Schema(
                    type=genai.types.Type.STRING,
                    enum=["MASSA", "RECHEIO", "DECORACAO"],
                    description="Tipo de item a consultar",
                ),
                "period_type": genai.types.Schema(
                    type=genai.types.Type.STRING,
                    enum=["MES", "ANO"],
                    description="Agrupamento temporal",
                ),
                "year": genai.types.Schema(
                    type=genai.types.Type.INTEGER,
                    description="Ano para filtrar",
                ),
            },
            required=["item_type", "period_type"],
        ),
    ),
]


async def execute(
    name: str, args: dict, base_url: str, token: str | None, client: httpx.AsyncClient
) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    params: dict = {}

    if name == "get_cake_orders_by_period":
        url = f"{base_url}/dashboard/qtdPedidosBoloPorPeriodo"
        params["periodo"] = args.get("period_type", "MES")
        if "year" in args:
            params["ano"] = args["year"]

    elif name == "get_batch_orders_by_period":
        url = f"{base_url}/dashboard/qtdPedidosFornadaPorPeriodo"
        params["periodo"] = args.get("period_type", "MES")
        if "year" in args:
            params["ano"] = args["year"]

    elif name == "get_items_by_period":
        url = f"{base_url}/dashboard/itens-mais-pedidos-por-periodo"
        params["tipoItem"] = args.get("item_type", "MASSA")
        params["periodo"] = args.get("period_type", "MES")
        if "year" in args:
            params["ano"] = args["year"]
    else:
        return {"error": f"Tool desconhecida: {name}"}

    resp = await client.get(url, params=params, headers=headers)
    if resp.status_code == 204:
        return {"data": [], "message": "Nenhum dado encontrado"}
    resp.raise_for_status()
    return resp.json()
