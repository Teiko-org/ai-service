"""Texto de status da fornada apos commits de escrita (produtos atuais)."""

from __future__ import annotations

from app.tools.registry import execute_tool
from app.tools.writes.catalog_display import aggregate_fornada_products

_FORNADA_WRITE_ACTIONS = frozenset(
    {"create_batch", "add_batch_lines", "replace_active_batch"}
)


def fornada_id_from_write_result(action: str, result: dict) -> int | None:
    if not result.get("ok") or action not in _FORNADA_WRITE_ACTIONS:
        return None
    fid = result.get("fornada_id")
    if isinstance(fid, int) and fid > 0:
        return fid
    data = result.get("data")
    if isinstance(data, dict):
        raw = data.get("id")
        if isinstance(raw, int) and raw > 0:
            return raw
    return None


async def format_fornada_products_block(
    fornada_id: int, base_url: str, token: str | None
) -> str | None:
    data = await execute_tool(
        "get_products_in_batch",
        {"batch_id": fornada_id},
        base_url,
        token,
    )
    if data.get("error"):
        return None
    produtos = data.get("produtos")
    if not isinstance(produtos, list):
        return None
    lines: list[str] = []
    for nome, total in aggregate_fornada_products(produtos):
        if total > 0:
            lines.append(f"- {nome} x{total}")
        else:
            lines.append(f"- {nome}")
    if not lines:
        return f"Fornada #{fornada_id} agora esta sem produtos cadastrados."
    return f"Fornada #{fornada_id} agora:\n" + "\n".join(lines)


async def append_fornada_summary_if_applicable(
    action: str, result: dict, answer: str, base_url: str, token: str | None
) -> str:
    fid = fornada_id_from_write_result(action, result)
    if fid is None:
        return answer
    block = await format_fornada_products_block(fid, base_url, token)
    if not block:
        return answer
    return f"{answer}\n\n{block}"
