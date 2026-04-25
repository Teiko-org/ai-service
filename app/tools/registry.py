import logging

from app.core.http_client import get_http_client
from app.tools import dashboard, orders, products, production, reports

logger = logging.getLogger(__name__)

TOOL_DECLARATIONS = (
    dashboard.DECLARATIONS
    + orders.DECLARATIONS
    + products.DECLARATIONS
    + production.DECLARATIONS
    + reports.DECLARATIONS
)

_EXECUTORS = {
    **{d.name: dashboard.execute for d in dashboard.DECLARATIONS},
    **{d.name: orders.execute for d in orders.DECLARATIONS},
    **{d.name: products.execute for d in products.DECLARATIONS},
    **{d.name: production.execute for d in production.DECLARATIONS},
    **{d.name: reports.execute for d in reports.DECLARATIONS},
}


async def execute_tool(
    name: str, args: dict, base_url: str, token: str | None = None
) -> dict:
    executor = _EXECUTORS.get(name)
    if not executor:
        logger.warning("Tool nao registrada: %s", name)
        return {"error": f"Tool '{name}' nao encontrada"}

    client = get_http_client()
    try:
        result = await executor(name, args, base_url, token, client)
        logger.info("Tool %s executada com sucesso", name)
        return result
    except Exception as exc:
        logger.error("Erro ao executar tool %s: %s", name, exc)
        return {"error": f"Falha ao buscar dados: {str(exc)}"}
