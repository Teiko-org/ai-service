"""Tools de gestao de fornadas.

Cobrem o FornadaController do backend Java: proxima fornada, listagem (ativas
ou todas), filtro por mes/ano e produtos por fornada. Sao tools READ-ONLY.
"""

import google.genai as genai
import httpx


DECLARATIONS = [
    genai.types.FunctionDeclaration(
        name="get_next_batch",
        description=(
            "Retorna a proxima fornada agendada (data de inicio futura mais "
            "proxima). Use quando o usuario perguntar 'qual a proxima fornada', "
            "'quando vai ter fornada' ou similar."
        ),
        parameters=genai.types.Schema(type=genai.types.Type.OBJECT, properties={}),
    ),
    genai.types.FunctionDeclaration(
        name="get_active_batches",
        description=(
            "Lista todas as fornadas ATIVAS (nao encerradas). Use quando o "
            "usuario perguntar pelas fornadas em andamento ou pedir 'lista de "
            "fornadas'."
        ),
        parameters=genai.types.Schema(type=genai.types.Type.OBJECT, properties={}),
    ),
    genai.types.FunctionDeclaration(
        name="get_all_batches",
        description=(
            "Lista todas as fornadas (ativas e encerradas). Use quando o "
            "usuario quiser ver o historico completo, nao apenas as em "
            "andamento."
        ),
        parameters=genai.types.Schema(type=genai.types.Type.OBJECT, properties={}),
    ),
    genai.types.FunctionDeclaration(
        name="get_batches_by_month",
        description=(
            "Lista fornadas de um mes/ano especifico, com os itens (produtos) "
            "de cada fornada. Util para 'quais fornadas tivemos em janeiro?' "
            "ou 'me mostra as fornadas de marco/2026'."
        ),
        parameters=genai.types.Schema(
            type=genai.types.Type.OBJECT,
            properties={
                "year": genai.types.Schema(
                    type=genai.types.Type.INTEGER,
                    description="Ano (ex: 2026).",
                ),
                "month": genai.types.Schema(
                    type=genai.types.Type.INTEGER,
                    description="Mes (1-12).",
                ),
            },
            required=["year", "month"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="get_products_in_batch",
        description=(
            "Lista os produtos de uma fornada especifica (pelo ID da fornada), "
            "com produto, quantidade total, valor unitario, etc."
        ),
        parameters=genai.types.Schema(
            type=genai.types.Type.OBJECT,
            properties={
                "batch_id": genai.types.Schema(
                    type=genai.types.Type.INTEGER,
                    description="ID da fornada.",
                ),
            },
            required=["batch_id"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="get_latest_batch_products",
        description=(
            "Lista os produtos da fornada MAIS RECENTE (ultima criada). Use "
            "quando o usuario perguntar 'o que tem na fornada da vez', 'quais "
            "produtos estao na ultima fornada' etc."
        ),
        parameters=genai.types.Schema(type=genai.types.Type.OBJECT, properties={}),
    ),
]


async def execute(
    name: str, args: dict, base_url: str, token: str | None, client: httpx.AsyncClient
) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    if name == "get_next_batch":
        url = f"{base_url}/fornadas/proxima"
        resp = await client.get(url, headers=headers)
        if resp.status_code == 204:
            return {"data": None, "message": "Nenhuma fornada futura agendada."}
        resp.raise_for_status()
        return resp.json()

    if name == "get_active_batches":
        url = f"{base_url}/fornadas"
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return {"data": resp.json()}

    if name == "get_all_batches":
        url = f"{base_url}/fornadas/todas"
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return {"data": resp.json()}

    if name == "get_batches_by_month":
        year = args.get("year")
        month = args.get("month")
        if year is None or month is None:
            return {"error": "year e month sao obrigatorios."}
        url = f"{base_url}/fornadas/com-itens"
        resp = await client.get(
            url, params={"ano": year, "mes": month}, headers=headers
        )
        resp.raise_for_status()
        return {"data": resp.json()}

    if name == "get_products_in_batch":
        batch_id = args.get("batch_id")
        if batch_id is None:
            return {"error": "batch_id e obrigatorio."}
        url = f"{base_url}/fornadas/da-vez/produtos/{batch_id}"
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return {"data": resp.json()}

    if name == "get_latest_batch_products":
        url = f"{base_url}/fornadas/mais-recente/produtos"
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return {"data": resp.json()}

    return {"error": f"Tool desconhecida: {name}"}
