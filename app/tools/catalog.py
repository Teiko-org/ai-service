"""Tools de catalogo (cardapio): produtos cadastrados, decoracoes, tamanhos
e formatos de bolo. Sao todas READ-ONLY."""

import google.genai as genai
import httpx


DECLARATIONS = [
    genai.types.FunctionDeclaration(
        name="get_registered_products",
        description=(
            "Lista produtos cadastrados (tipo FORNADA e BOLO). Use para listar "
            "cardapio ao usuario ou para conferir nomes — em add_batch_lines "
            "prefira enviar produto_nome e deixar o servidor resolver o id."
        ),
        parameters=genai.types.Schema(type=genai.types.Type.OBJECT, properties={}),
    ),
    genai.types.FunctionDeclaration(
        name="get_decorations",
        description=(
            "Lista as decoracoes ativas disponiveis para bolos. Use quando o "
            "usuario perguntar pelas decoracoes do cardapio."
        ),
        parameters=genai.types.Schema(type=genai.types.Type.OBJECT, properties={}),
    ),
    genai.types.FunctionDeclaration(
        name="get_cake_sizes",
        description=(
            "Lista os tamanhos de bolo disponiveis (enum TamanhoEnum). Use "
            "quando o usuario perguntar 'quais tamanhos temos?'."
        ),
        parameters=genai.types.Schema(type=genai.types.Type.OBJECT, properties={}),
    ),
    genai.types.FunctionDeclaration(
        name="get_cake_formats",
        description=(
            "Lista os formatos de bolo disponiveis (enum FormatoEnum). Use "
            "quando o usuario perguntar 'quais formatos temos?'."
        ),
        parameters=genai.types.Schema(type=genai.types.Type.OBJECT, properties={}),
    ),
    genai.types.FunctionDeclaration(
        name="get_doughs_catalog",
        description=(
            "Lista as massas (sabores) cadastradas. Util para descobrir o ID "
            "de uma massa antes de filtrar pedidos por massa."
        ),
        parameters=genai.types.Schema(type=genai.types.Type.OBJECT, properties={}),
    ),
    genai.types.FunctionDeclaration(
        name="get_fillings_catalog",
        description=(
            "Lista os recheios unitarios cadastrados (sabores individuais). "
            "Para combinacoes nomeadas (Hugo, Dora, Bia Benego...) use "
            "get_exclusive_fillings_catalog."
        ),
        parameters=genai.types.Schema(type=genai.types.Type.OBJECT, properties={}),
    ),
    genai.types.FunctionDeclaration(
        name="get_exclusive_fillings_catalog",
        description=(
            "Lista os recheios exclusivos (combinacoes nomeadas de dois unitarios, "
            "ex.: Hugo = brigadeiro meio amargo + brigadeiro de ninho). Use quando "
            "o usuario citar nome de recheio que nao seja sabor unico."
        ),
        parameters=genai.types.Schema(type=genai.types.Type.OBJECT, properties={}),
    ),
]


_ENDPOINTS = {
    "get_registered_products": "/dashboard/produtosCadastrados",
    "get_decorations": "/decoracoes",
    "get_cake_sizes": "/bolos/tamanhos",
    "get_cake_formats": "/bolos/formatos",
    "get_doughs_catalog": "/bolos/massa",
    "get_fillings_catalog": "/bolos/recheio-unitario",
    "get_exclusive_fillings_catalog": "/bolos/recheio-exclusivo",
}


async def execute(
    name: str, args: dict, base_url: str, token: str | None, client: httpx.AsyncClient
) -> dict:
    endpoint = _ENDPOINTS.get(name)
    if endpoint is None:
        return {"error": f"Tool desconhecida: {name}"}

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    url = f"{base_url}{endpoint}"
    resp = await client.get(url, headers=headers)
    if resp.status_code == 204:
        return {"data": [], "message": "Nenhum item cadastrado."}
    resp.raise_for_status()
    payload = resp.json()
    if isinstance(payload, list):
        return {"data": payload}
    return payload
