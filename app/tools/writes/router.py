"""Central router for all V3 write tools."""

from __future__ import annotations

import httpx

from app.tools.writes import fornada, pedido_bolo

WRITE_TOOL_NAMES = fornada.WRITE_TOOL_NAMES | pedido_bolo.WRITE_TOOL_NAMES

DECLARATIONS = fornada.DECLARATIONS + pedido_bolo.DECLARATIONS


async def execute(
    name: str, args: dict, base_url: str, token: str | None, client: httpx.AsyncClient
) -> dict:
    if name in fornada.WRITE_TOOL_NAMES:
        return await fornada.execute(name, args, base_url, token, client)
    if name in pedido_bolo.WRITE_TOOL_NAMES:
        return await pedido_bolo.execute(name, args, base_url, token, client)
    return {"error": f"Tool desconhecida: {name}"}
