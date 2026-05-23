"""Tools de gestao de fornadas.

Cobrem o FornadaController do backend Java: proxima fornada, listagem (ativas
ou todas), filtro por mes/ano e produtos por fornada. Sao tools READ-ONLY.
"""

from __future__ import annotations

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
            "Lista todas as fornadas (ativas e encerradas), com datas e status. "
            "Use para 'tem fornada em 2025?', 'quais fornadas existem' ou filtrar "
            "por ano no JSON retornado. NAO confundir com get_latest_batch_kpi "
            "(so a ultima encerrada)."
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
        name="get_active_batch_with_products",
        description=(
            "Fornada ATIVA agora + lista de produtos/quantidades dessa fornada. "
            "Use SEMPRE que o usuario pedir 'fornada ativa', 'fornada em andamento' "
            "e seus produtos — uma chamada so. Nao use get_latest_batch_products para isso."
        ),
        parameters=genai.types.Schema(type=genai.types.Type.OBJECT, properties={}),
    ),
    genai.types.FunctionDeclaration(
        name="get_latest_batch_products",
        description=(
            "Atalho legado: produtos da fornada ativa por data de inicio mais recente. "
            "Prefira get_active_batch_with_products. Para historico por mes, "
            "use get_batches_by_month."
        ),
        parameters=genai.types.Schema(type=genai.types.Type.OBJECT, properties={}),
    ),
]


from app.tools.writes.batch_overlap import (  # noqa: E402  (post-DECLARATIONS import)
    pick_active_batch as _pick_primary_active_batch,
)


def _simplify_product_row(row: dict) -> dict:
    return {
        "produto": row.get("produto") or row.get("nome"),
        "quantidade": row.get("quantidade"),
        "quantidade_vendida": row.get("quantidadeVendida"),
        "valor": row.get("valor"),
        "categoria": row.get("categoria"),
    }


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
        raw = resp.json()
        items = raw if isinstance(raw, list) else []
        return {
            "fornada_id": batch_id,
            "produtos": [_simplify_product_row(r) for r in items if isinstance(r, dict)],
        }

    if name == "get_active_batch_with_products":
        resp = await client.get(f"{base_url}/fornadas", headers=headers)
        resp.raise_for_status()
        payload = resp.json()
        items = payload if isinstance(payload, list) else []
        active = _pick_primary_active_batch(items)
        if not active:
            return {
                "fornada_ativa": None,
                "message": "Nenhuma fornada ativa no momento.",
                "produtos": [],
            }
        fid = active.get("id")
        if not isinstance(fid, int):
            return {"error": "Fornada ativa sem ID valido."}
        prod_resp = await client.get(
            f"{base_url}/fornadas/da-vez/produtos/{fid}", headers=headers
        )
        prod_resp.raise_for_status()
        raw_products = prod_resp.json()
        product_list = (
            raw_products if isinstance(raw_products, list) else []
        )
        return {
            "fornada_ativa": {
                "id": fid,
                "data_inicio": str(active.get("dataInicio") or active.get("data_inicio"))[
                    :10
                ],
                "data_fim": str(active.get("dataFim") or active.get("data_fim"))[:10],
            },
            "produtos": [
                _simplify_product_row(r) for r in product_list if isinstance(r, dict)
            ],
            "instruction": (
                "Apresente o periodo da fornada_ativa e a lista produtos com quantidades. "
                "Ignore campos de data antigos dentro de cada produto — use so fornada_ativa."
            ),
        }

    if name == "get_latest_batch_products":
        url = f"{base_url}/fornadas/mais-recente/produtos"
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return {"data": resp.json()}

    return {"error": f"Tool desconhecida: {name}"}
