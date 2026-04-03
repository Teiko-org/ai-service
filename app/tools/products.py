import google.genai as genai
import httpx

DECLARATIONS = [
    genai.types.FunctionDeclaration(
        name="get_latest_batch_kpi",
        description=(
            "Retorna os KPIs da ultima fornada encerrada: quantidade disponivel vs vendida, "
            "valor total vs arrecadado, percentual de aproveitamento e valor perdido."
        ),
        parameters=genai.types.Schema(type=genai.types.Type.OBJECT, properties={}),
    ),
    genai.types.FunctionDeclaration(
        name="get_batch_kpi_by_period",
        description=(
            "Retorna KPIs agregados de todas as fornadas em um periodo especifico. "
            "Util para avaliar desempenho geral das fornadas ao longo do tempo."
        ),
        parameters=genai.types.Schema(
            type=genai.types.Type.OBJECT,
            properties={
                "period_type": genai.types.Schema(
                    type=genai.types.Type.STRING,
                    enum=["MES", "ANO"],
                    description="Agrupamento: MES ou ANO",
                ),
                "year": genai.types.Schema(
                    type=genai.types.Type.INTEGER,
                    description="Ano para filtrar",
                ),
                "month": genai.types.Schema(
                    type=genai.types.Type.INTEGER,
                    description="Mes para filtrar (1-12), apenas quando period_type=MES",
                ),
            },
            required=["period_type"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="get_batch_kpi_by_id",
        description="Retorna os KPIs de uma fornada especifica pelo seu ID.",
        parameters=genai.types.Schema(
            type=genai.types.Type.OBJECT,
            properties={
                "batch_id": genai.types.Schema(
                    type=genai.types.Type.INTEGER,
                    description="ID da fornada",
                ),
            },
            required=["batch_id"],
        ),
    ),
]


async def execute(
    name: str, args: dict, base_url: str, token: str | None, client: httpx.AsyncClient
) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    params: dict = {}

    if name == "get_latest_batch_kpi":
        url = f"{base_url}/dashboard/kpi-fornada-mais-recente"

    elif name == "get_batch_kpi_by_period":
        url = f"{base_url}/dashboard/kpi-fornadas-por-periodo"
        params["periodo"] = args.get("period_type", "MES")
        if "year" in args:
            params["ano"] = args["year"]
        if "month" in args:
            params["mes"] = args["month"]

    elif name == "get_batch_kpi_by_id":
        batch_id = args.get("batch_id", 0)
        url = f"{base_url}/dashboard/kpi-fornada/{batch_id}"
    else:
        return {"error": f"Tool desconhecida: {name}"}

    resp = await client.get(url, params=params, headers=headers)
    if resp.status_code == 204:
        return {"data": [], "message": "Nenhum dado encontrado"}
    resp.raise_for_status()
    return resp.json()
