"""Tools de escrita para fornadas (V3).

Cobre create_batch, add_batch_lines, close_batch e replace_active_batch.
Two-step com HMAC (confirm_token) por `_helpers`.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import google.genai as genai
import httpx

from app.core.request_context import current_session_id
from app.core.sessions import session_store
from app.tools.writes._helpers import (
    WriteToolError,
    check_throttle,
    delete_request,
    get_json,
    effective_confirmed,
    issue_preview,
    post_json,
    preview_response,
    require_auth,
    run_idempotent_commit,
    verify_commit,
)
from app.tools.writes.batch_overlap import (
    active_batch_error_message,
    find_active_batch,
)
from app.tools.writes.dates import parse_user_date
from app.tools.writes.product_resolve import (
    canonical_lines_for_hmac,
    resolve_batch_lines,
)
from app.tools.writes.schema_shared import confirm_token_param, confirmed_param


WRITE_TOOL_NAMES = {
    "create_batch",
    "add_batch_lines",
    "close_batch",
    "replace_active_batch",
}

_MAX_BATCH_HORIZON_DAYS = 365
_MAX_LINES_PER_CALL = 50
_MAX_QUANTITY = 10_000
_MAX_CLOSE_PER_CALL = 20

# Aliases mantidos para compatibilidade com pedido_bolo.py e testes existentes.
_confirmed_param = confirmed_param
_confirm_token_param = confirm_token_param
_parse_user_date = parse_user_date


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
            "Adiciona produtos a uma fornada (POST /fornadas/da-vez). "
            "Use produto_nome (Pao Frances, Croissant, etc.) — o servidor resolve o ID. "
            "Se o usuario disser 'nessa fornada', 'a que acabamos de criar' ou nao souber "
            "o numero, OMITA fornada_id: o servidor usa a fornada ativa ou a ultima criada "
            "na sessao. NUNCA peca ID de fornada nem produto_fornada_id ao usuario. "
            "Two-step com confirmacao."
        ),
        parameters=genai.types.Schema(
            type=genai.types.Type.OBJECT,
            properties={
                "fornada_id": genai.types.Schema(
                    type=genai.types.Type.INTEGER,
                    description=(
                        "Opcional. Omita quando o usuario se referir a fornada ativa "
                        "ou a recém criada; o servidor resolve sozinho."
                    ),
                ),
                "lines": genai.types.Schema(
                    type=genai.types.Type.ARRAY,
                    description=(
                        "Lista de linhas: produto_nome (preferido, texto do usuario) "
                        "ou produto_fornada_id, e quantidade (int>=1)."
                    ),
                    items=genai.types.Schema(
                        type=genai.types.Type.OBJECT,
                        properties={
                            "produto_nome": genai.types.Schema(
                                type=genai.types.Type.STRING,
                                description=(
                                    "Nome do produto de fornada como o usuario fala "
                                    "(ex.: Pao Frances, Croissant, paes)."
                                ),
                            ),
                            "produto_fornada_id": genai.types.Schema(
                                type=genai.types.Type.INTEGER,
                                description=(
                                    "Opcional se produto_nome for informado; "
                                    "nao peca isso ao usuario."
                                ),
                            ),
                            "quantidade": genai.types.Schema(
                                type=genai.types.Type.INTEGER
                            ),
                        },
                        required=["quantidade"],
                    ),
                ),
                "confirmed": _confirmed_param(),
                "confirm_token": _confirm_token_param(),
            },
            required=["lines"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="close_batch",
        description=(
            "Encerra (desativa) uma ou mais fornadas — mesmo efeito do app ao cancelar. "
            "Se o usuario pedir varias (ex.: 'encerre 10 e 11'), passe fornada_ids [10, 11] "
            "numa unica chamada e uma confirmacao. Para uma so, fornada_id ou fornada_ids "
            "com um elemento. Two-step com confirmacao."
        ),
        parameters=genai.types.Schema(
            type=genai.types.Type.OBJECT,
            properties={
                "fornada_id": genai.types.Schema(
                    type=genai.types.Type.INTEGER,
                    description="ID de uma fornada (atalho quando e so uma).",
                ),
                "fornada_ids": genai.types.Schema(
                    type=genai.types.Type.ARRAY,
                    items=genai.types.Schema(type=genai.types.Type.INTEGER),
                    description=(
                        "Lista de IDs a encerrar (ex.: [10, 11]). Preferir quando o "
                        "usuario citar mais de uma fornada."
                    ),
                ),
                "confirmed": _confirmed_param(),
                "confirm_token": _confirm_token_param(),
            },
        ),
    ),
    genai.types.FunctionDeclaration(
        name="replace_active_batch",
        description=(
            "Substitui a fornada ativa atual: encerra a que esta rolando e cria a nova "
            "com as datas informadas, em uma unica confirmacao. Use quando o usuario "
            "quer trocar de fornada e ja existe uma ativa (create_batch vai falhar). "
            "Two-step: confirmed=False primeiro, depois confirmed=True + confirm_token."
        ),
        parameters=genai.types.Schema(
            type=genai.types.Type.OBJECT,
            properties={
                "data_inicio": genai.types.Schema(
                    type=genai.types.Type.STRING,
                    description="Data de inicio da nova fornada (yyyy-MM-dd).",
                ),
                "data_fim": genai.types.Schema(
                    type=genai.types.Type.STRING,
                    description="Data de fim da nova fornada (yyyy-MM-dd).",
                ),
                "confirmed": _confirmed_param(),
                "confirm_token": _confirm_token_param(),
            },
            required=["data_inicio", "data_fim"],
        ),
    ),
]


def _validate_create_batch_args(args: dict) -> tuple[str, str]:
    data_inicio = parse_user_date(args.get("data_inicio"), "data_inicio")
    data_fim = parse_user_date(args.get("data_fim"), "data_fim")
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


def _remember_fornada_id(fornada_id: int) -> None:
    sid = current_session_id.get()
    if sid:
        session_store.set_last_fornada_id(sid, fornada_id)


def _fornada_id_from_payload(data: Any) -> int | None:
    if isinstance(data, dict):
        fid = data.get("id")
        if isinstance(fid, int) and fid > 0:
            return fid
    return None


async def _resolve_fornada_id_for_lines(
    args: dict,
    client: httpx.AsyncClient,
    base_url: str,
    token: str | None,
) -> int:
    fid = args.get("fornada_id")
    if isinstance(fid, int) and fid > 0:
        return fid

    sid = current_session_id.get()
    if sid:
        last = session_store.get_last_fornada_id(sid)
        if isinstance(last, int) and last > 0:
            return last

    active = await find_active_batch(client, base_url, token)
    if active:
        resolved = _fornada_id_from_payload(active)
        if resolved is not None:
            return resolved

    raise WriteToolError(
        "Nao identifiquei qual fornada usar. Crie uma fornada antes ou diga o numero "
        "(ex.: fornada #12)."
    )


async def _validate_add_batch_lines_args(
    args: dict,
    base_url: str,
    token: str | None,
    client: httpx.AsyncClient,
) -> tuple[int, list[dict]]:
    fornada_id = await _resolve_fornada_id_for_lines(
        args, client, base_url, token
    )

    raw_lines = args.get("lines")
    if not isinstance(raw_lines, list) or not raw_lines:
        raise WriteToolError("lines deve ser uma lista nao vazia.")
    if len(raw_lines) > _MAX_LINES_PER_CALL:
        raise WriteToolError(
            f"Muitas linhas em uma so chamada (max {_MAX_LINES_PER_CALL})."
        )

    normalized = await resolve_batch_lines(raw_lines, client, base_url, token)
    for idx, line in enumerate(normalized, start=1):
        qty = line["quantidade"]
        if qty > _MAX_QUANTITY:
            raise WriteToolError(
                f"Linha {idx}: quantidade deve ser inteiro entre 1 e {_MAX_QUANTITY}."
            )
    return fornada_id, normalized


def _resolve_close_fornada_ids(args: dict) -> list[int]:
    ids: list[int] = []
    single = args.get("fornada_id")
    if isinstance(single, int) and single > 0:
        ids.append(single)
    multi = args.get("fornada_ids")
    if isinstance(multi, list):
        for item in multi:
            if isinstance(item, int) and item > 0:
                ids.append(item)
    if not ids:
        raise WriteToolError(
            "Informe fornada_id (uma) ou fornada_ids (lista) com IDs positivos."
        )
    unique = sorted(set(ids))
    if len(unique) > _MAX_CLOSE_PER_CALL:
        raise WriteToolError(
            f"Muitas fornadas em uma chamada (max {_MAX_CLOSE_PER_CALL})."
        )
    return unique


async def _close_batch_preview_labels(
    fornada_ids: list[int],
    base_url: str,
    token: str | None,
    client: httpx.AsyncClient,
) -> list[str]:
    labels: list[str] = []
    for fid in fornada_ids:
        label = f"#{fid}"
        try:
            detail = await get_json(client, f"{base_url}/fornadas/{fid}", token)
            if isinstance(detail, dict):
                period = _format_batch_period_label(detail, "", "")
                label = f"#{fid} ({period})"
        except WriteToolError:
            pass
        labels.append(label)
    return labels


def _format_batch_period_label(batch: dict | None, fallback_start: str, fallback_end: str) -> str:
    if not batch:
        return f"{fallback_start} a {fallback_end}"
    start = batch.get("dataInicio") or batch.get("data_inicio") or fallback_start
    end = batch.get("dataFim") or batch.get("data_fim") or fallback_end
    return f"{str(start)[:10]} a {str(end)[:10]}"


async def _execute_create_batch(
    args: dict, base_url: str, token: str | None, client: httpx.AsyncClient
) -> dict:
    confirmed = effective_confirmed(args)
    data_inicio_str, data_fim_str = _validate_create_batch_args(args)
    canonical_args = {"data_inicio": data_inicio_str, "data_fim": data_fim_str}

    active = await find_active_batch(client, base_url, token)
    if active:
        if not confirmed:
            return await _execute_replace_active_batch(
                {
                    "data_inicio": args.get("data_inicio"),
                    "data_fim": args.get("data_fim"),
                    "confirmed": False,
                },
                base_url,
                token,
                client,
            )
        return {"error": active_batch_error_message(active)}

    if not confirmed:
        confirm_token = issue_preview("create_batch", canonical_args)
        return preview_response(
            tool_name="create_batch",
            confirm_token=confirm_token,
            payload=canonical_args,
            message=(
                f"Vou criar uma fornada de {data_inicio_str} a {data_fim_str}. Confirma?"
            ),
        )

    confirm_token = args.get("confirm_token", "")

    async def _commit() -> dict:
        require_auth(token)
        verify_commit("create_batch", canonical_args, confirm_token)
        check_throttle("create_batch")
        body = {"dataInicio": data_inicio_str, "dataFim": data_fim_str}
        data = await post_json(client, f"{base_url}/fornadas", body, token)
        fid = _fornada_id_from_payload(data)
        if fid is not None:
            _remember_fornada_id(fid)
        return {"ok": True, "action": "create_batch", "data": data, "fornada_id": fid}

    return await run_idempotent_commit(confirm_token, _commit)


async def _execute_add_batch_lines(
    args: dict, base_url: str, token: str | None, client: httpx.AsyncClient
) -> dict:
    confirmed = effective_confirmed(args)
    fornada_id, lines = await _validate_add_batch_lines_args(
        args, base_url, token, client
    )
    canonical_args = {
        "fornada_id": fornada_id,
        "lines": canonical_lines_for_hmac(lines),
    }

    if not confirmed:
        confirm_token = issue_preview("add_batch_lines", canonical_args)
        summary = ", ".join(
            f"{ln.get('produto_nome', 'produto')} x{ln['quantidade']}" for ln in lines
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


async def _execute_close_batch(
    args: dict, base_url: str, token: str | None, client: httpx.AsyncClient
) -> dict:
    confirmed = effective_confirmed(args)
    try:
        fornada_ids = _resolve_close_fornada_ids(args)
    except WriteToolError as exc:
        return {"error": str(exc)}

    canonical_args = {"fornada_ids": fornada_ids}

    if not confirmed:
        labels = await _close_batch_preview_labels(
            fornada_ids, base_url, token, client
        )
        if len(labels) == 1:
            msg = f"Vou encerrar a fornada {labels[0]}. Confirma?"
        else:
            joined = " e ".join(labels)
            msg = f"Vou encerrar as fornadas {joined}. Confirma?"
        confirm_token = issue_preview("close_batch", canonical_args)
        return preview_response(
            tool_name="close_batch",
            confirm_token=confirm_token,
            payload=canonical_args,
            message=msg,
        )

    confirm_token = args.get("confirm_token", "")

    async def _commit() -> dict:
        require_auth(token)
        verify_commit("close_batch", canonical_args, confirm_token)
        check_throttle("close_batch")
        for fid in fornada_ids:
            await delete_request(client, f"{base_url}/fornadas/{fid}", token)
        result: dict[str, Any] = {
            "ok": True,
            "action": "close_batch",
            "fornada_ids": fornada_ids,
        }
        if len(fornada_ids) == 1:
            result["fornada_id"] = fornada_ids[0]
        return result

    return await run_idempotent_commit(confirm_token, _commit)


async def _execute_replace_active_batch(
    args: dict, base_url: str, token: str | None, client: httpx.AsyncClient
) -> dict:
    confirmed = effective_confirmed(args)
    data_inicio_str, data_fim_str = _validate_create_batch_args(args)

    active = await find_active_batch(client, base_url, token)
    if not active:
        return {
            "error": (
                "Nao ha fornada ativa para substituir. "
                "Use create_batch para abrir uma nova fornada."
            )
        }

    close_id = active.get("id")
    if not isinstance(close_id, int) or close_id <= 0:
        return {"error": "Fornada ativa sem ID valido no sistema."}

    canonical_args = {
        "close_fornada_id": close_id,
        "data_inicio": data_inicio_str,
        "data_fim": data_fim_str,
    }
    old_label = f"#{close_id} ({_format_batch_period_label(active, '', '')})"

    if not confirmed:
        confirm_token = issue_preview("replace_active_batch", canonical_args)
        return preview_response(
            tool_name="replace_active_batch",
            confirm_token=confirm_token,
            payload=canonical_args,
            message=(
                f"Vou encerrar a fornada {old_label} e criar uma nova de "
                f"{data_inicio_str} a {data_fim_str}. Confirma?"
            ),
        )

    confirm_token = args.get("confirm_token", "")

    async def _commit() -> dict:
        require_auth(token)
        verify_commit("replace_active_batch", canonical_args, confirm_token)
        check_throttle("replace_active_batch")
        await delete_request(client, f"{base_url}/fornadas/{close_id}", token)
        body = {"dataInicio": data_inicio_str, "dataFim": data_fim_str}
        try:
            data = await post_json(client, f"{base_url}/fornadas", body, token)
        except WriteToolError as exc:
            raise WriteToolError(
                f"A fornada #{close_id} foi encerrada, mas nao foi possivel criar a nova: "
                f"{exc}. Crie a fornada de novo com create_batch."
            ) from exc
        new_id = _fornada_id_from_payload(data)
        if new_id is not None:
            _remember_fornada_id(new_id)
        return {
            "ok": True,
            "action": "replace_active_batch",
            "closed_fornada_id": close_id,
            "data": data,
            "fornada_id": new_id,
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
        if name == "close_batch":
            return await _execute_close_batch(args, base_url, token, client)
        if name == "replace_active_batch":
            return await _execute_replace_active_batch(args, base_url, token, client)
        return {"error": f"Tool desconhecida: {name}"}
    except WriteToolError as exc:
        return {"error": str(exc)}
