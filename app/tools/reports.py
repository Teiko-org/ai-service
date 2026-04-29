"""Tool para sinalizar geracao de relatorio PDF de insights.

Esta tool nao chama o backend; ela apenas sinaliza ao frontend que o usuario
deve baixar o PDF gerado em GET /relatorios/insights. O frontend detecta o uso
desta tool em tools_used e renderiza um botao de download.
"""

import google.genai as genai
import httpx

REPORT_TOOL_NAME = "generate_insights_report"
REPORT_ENDPOINT = "/relatorios/insights"
REPORT_FILENAME = "relatorio-insights.pdf"

DECLARATIONS = [
    genai.types.FunctionDeclaration(
        name=REPORT_TOOL_NAME,
        description=(
            "Sinaliza que o usuario solicitou um relatorio PDF dos insights de pedidos. "
            "Use SEMPRE que o usuario pedir um relatorio, PDF, exportacao, "
            "documento, resumo em arquivo ou similar. "
            "Esta funcao apenas marca a intencao — o frontend exibira um botao para download. "
            "Apos chamar, responda confirmando que o relatorio foi gerado e oriente o usuario "
            "a clicar no botao de download que aparecera abaixo da resposta."
        ),
        parameters=genai.types.Schema(type=genai.types.Type.OBJECT, properties={}),
    ),
]


async def execute(
    name: str, args: dict, base_url: str, token: str | None, client: httpx.AsyncClient
) -> dict:
    return {
        "ready": True,
        "filename": REPORT_FILENAME,
        "endpoint": REPORT_ENDPOINT,
        "message": (
            "Relatorio preparado. O usuario podera baixar o PDF clicando no botao "
            "que sera exibido abaixo desta mensagem."
        ),
    }
