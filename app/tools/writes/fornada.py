"""Write tools for fornada management (V3 phase 1).

- create_batch:        POST /fornadas
- add_batch_lines:     POST /fornadas/da-vez (one call per line)
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import google.genai as genai
import httpx

from app.tools.writes._helpers import (
    WriteToolError,
    check_throttle,
    issue_preview,
    post_json,
    preview_response,
    require_auth,
    run_idempotent_commit,
    verify_commit,
)


WRITE_TOOL_NAMES = {"create_batch", "add_batch_lines"}

_MAX_BATCH_HORIZON_DAYS = 365
_MAX_LINES_PER_CALL = 50
_MAX_QUANTITY = 10_000


def _confirmed_param() -> genai.types.Schema:
    return genai.types.Schema(
        type=genai.types.Type.BOOLEAN,
        description=(
            "False (padrao) retorna apenas a previa para confirmacao; "
            "True executa de fato. NUNCA passe True sem ter recebido "
            "confirmacao explicita do usuario em mensagem posterior a previa."
        ),
    )


def _confirm_token_param() -> genai.types.Schema:
    return genai.types.Schema(
        type=genai.types.Type.STRING,
        description=(
            "Token retornado pela tool no preview (campo confirm_token). "
            "Obrigatorio quando confirmed=True. Deve ser exatamente o mesmo "
            "valor recebido na previa, sem nenhuma alteracao."
        ),
    )


DECLARATIONS = [
    genai.types.FunctionDeclaration(
        name="create_batch",
        description=(
            "Cria uma nova fornada (periodo de producao). Acao destrutiva: "
            "SEMPRE chame primeiro com confirmed=False, mostre a previa ao "
            "usuario e SO chame com confirmed=True + confirm_token apos "
            "confirmacao explicita em uma NOVA mensagem do usuario."
        ),
        parameters=genai.types.Schema(
            type=genai.types.Type.OBJECT,
            properties={
                "data_inicio": genai.types.Schema(
                    type=genai.types.Type.STRING,
                    description="Data de inicio da fornada no formato yyyy-MM-dd.",
                ),
                "data_fim": genai.types.Schema(
                    type=genai.types.Type.STRING,
                    description="Data de fim da fornada no formato yyyy-MM-dd.",
                ),
                "confirmed": _confirmed_param(),
                "confirm_token": _confirm_token_param(),
            },
            required=["data_inicio", "data_fim"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="add_batch_lines",
        description=(
            "Adiciona um ou mais produtos (com quantidade) a uma fornada "
            "existente. Cada linha vira um POST em /fornadas/da-vez. Acao "
            "destrutiva: siga o mesmo fluxo two-step de confirmacao."
        ),
        parameters=genai.types.Schema(
            type=genai.types.Type.OBJECT,
            properties={
                "fornada_id": genai.types.Schema(
                    type=genai.types.Type.INTEGER,
                    description="ID da fornada existente que recebera os produtos.",
                ),
                "lines": genai.types.Schema(
                    type=genai.types.Type.ARRAY,
                    description=(
                        "Lista de linhas a adicionar; cada item tem "
                        "produto_fornada_id (int) e quantidade (int>=1)."
                    ),
                    items=genai.types.Schema(
                        type=genai.types.Type.OBJECT,
                        properties={
                            "produto_fornada_id": genai.types.Schema(
                                type=genai.types.Type.INTEGER
                            ),
                            "quantidade": genai.types.Schema(
                                type=genai.types.Type.INTEGER
                            ),
                        },
                        required=["produto_fornada_id", "quantidade"],
                    ),
                ),
                "confirmed": _confirmed_param(),
                "confirm_token": _confirm_token_param(),
            },
            required=["fornada_id", "lines"],
        ),
    ),
]


def _parse_iso_date(value: Any, field: str) -> date:
    if not isinstance(value, str):
        raise WriteToolError(f"Campo {field} deve ser uma data no formato yyyy-MM-dd.")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise WriteToolError(
            f"Campo {field} invalido: '{value}' nao esta no formato yyyy-MM-dd."
        ) from exc


def _validate_create_batch_args(args: dict) -> tuple[str, str]:
    data_inicio = _parse_iso_date(args.get("data_inicio"), "data_inicio")
    data_fim = _parse_iso_date(args.get("data_fim"), "data_fim")
    today = date.today()

    if data_inicio < today:
        raise WriteToolError("data_inicio nao pode estar no passado.")
    if data_fim < data_inicio:
        raise WriteToolError("data_fim deve ser maior ou igual a data_inicio.")
    if data_fim > today + timedelta(days=_MAX_BATCH_HORIZON_DAYS):
        raise WriteToolError(
            f"data_fim muito distante: limite e {_MAX_BATCH_HORIZON_DAYS} dias a partir de hoje."
        )
    return data_inicio.isoformat(), data_fim.isoformat()


def _validate_add_batch_lines_args(args: dict) -> tuple[int, list[dict]]:
    fornada_id = args.get("fornada_id")
    if not isinstance(fornada_id, int) or fornada_id <= 0:
        raise WriteToolError("fornada_id deve ser um inteiro positivo.")

    raw_lines = args.get("lines")
    if not isinstance(raw_lines, list) or not raw_lines:
        raise WriteToolError("lines deve ser uma lista nao vazia.")
    if len(raw_lines) > _MAX_LINES_PER_CALL:
        raise WriteToolError(
            f"Muitas linhas em uma so chamada (max {_MAX_LINES_PER_CALL})."
        )

    normalized: list[dict] = []
    for idx, line in enumerate(raw_lines, start=1):
        if not isinstance(line, dict):
            raise WriteToolError(f"Linha {idx} invalida: deve ser objeto.")
        pid = line.get("produto_fornada_id")
        qty = line.get("quantidade")
        if not isinstance(pid, int) or pid <= 0:
            raise WriteToolError(
                f"Linha {idx}: produto_fornada_id deve ser inteiro positivo."
            )
        if not isinstance(qty, int) or qty < 1 or qty > _MAX_QUANTITY:
            raise WriteToolError(
                f"Linha {idx}: quantidade deve ser inteiro entre 1 e {_MAX_QUANTITY}."
            )
        normalized.append({"produto_fornada_id": pid, "quantidade": qty})
    return fornada_id, normalized


async def _execute_create_batch(
    args: dict, base_url: str, token: str | None, client: httpx.AsyncClient
) -> dict:
    confirmed = bool(args.get("confirmed", False))
    data_inicio_str, data_fim_str = _validate_create_batch_args(args)
    canonical_args = {"data_inicio": data_inicio_str, "data_fim": data_fim_str}

    if not confirmed:
        confirm_token = issue_preview("create_batch", canonical_args)
        return preview_response(
            tool_name="create_batch",
            confirm_token=confirm_token,
            payload=canonical_args,
            message=(
                f"Vou criar uma fornada de {data_inicio_str} a {data_fim_str}. "
                "Confirma?"
            ),
        )

    confirm_token = args.get("confirm_token", "")

    async def _commit() -> dict:
        require_auth(token)
        verify_commit("create_batch", canonical_args, confirm_token)
        check_throttle("create_batch")
        body = {"dataInicio": data_inicio_str, "dataFim": data_fim_str}
        data = await post_json(client, f"{base_url}/fornadas", body, token)
        return {"ok": True, "action": "create_batch", "data": data}

    return await run_idempotent_commit(confirm_token, _commit)


async def _execute_add_batch_lines(
    args: dict, base_url: str, token: str | None, client: httpx.AsyncClient
) -> dict:
    confirmed = bool(args.get("confirmed", False))
    fornada_id, lines = _validate_add_batch_lines_args(args)
    canonical_args = {"fornada_id": fornada_id, "lines": lines}

    if not confirmed:
        confirm_token = issue_preview("add_batch_lines", canonical_args)
        summary = ", ".join(
            f"produto {ln['produto_fornada_id']} x{ln['quantidade']}" for ln in lines
        )
        return preview_response(
            tool_name="add_batch_lines",
            confirm_token=confirm_token,
            payload=canonical_args,
            message=(
                f"Vou adicionar {len(lines)} linha(s) na fornada #{fornada_id}: "
                f"{summary}. Confirma?"
            ),
        )

    confirm_token = args.get("confirm_token", "")

    async def _commit() -> dict:
        require_auth(token)
        verify_commit("add_batch_lines", canonical_args, confirm_token)
        check_throttle("add_batch_lines")
        created: list[dict] = []
        for line in lines:
            body = {
                "fornadaId": fornada_id,
                "produtoFornadaId": line["produto_fornada_id"],
                "quantidade": line["quantidade"],
            }
            data = await post_json(client, f"{base_url}/fornadas/da-vez", body, token)
            created.append(data)
        return {
            "ok": True,
            "action": "add_batch_lines",
            "fornada_id": fornada_id,
            "created": created,
        }

    return await run_idempotent_commit(confirm_token, _commit)


async def execute(
    name: str, args: dict, base_url: str, token: str | None, client: httpx.AsyncClient
) -> dict:
    try:
        if name == "create_batch":
            return await _execute_create_batch(args, base_url, token, client)
        if name == "add_batch_lines":
            return await _execute_add_batch_lines(args, base_url, token, client)
        return {"error": f"Tool desconhecida: {name}"}
    except WriteToolError as exc:
        return {"error": str(exc)}
