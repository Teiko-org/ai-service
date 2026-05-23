"""Helpers compartilhados pelas tools de escrita V3.

Cada tool segue o mesmo two-step:

    if not confirmed:
        validate_args(...)
        token = issue_preview(name, args)
        return preview_response(...)

    require_auth(bearer)
    check_throttle(name)
    verify_commit(name, args, confirm_token)
    post_json(...)

Centralizar aqui evita repetir auth, HMAC, throttle e idempotencia em cada tool.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core import confirm_tokens, write_throttle as wt_module
from app.core.cache import cache
from app.core.confirm_tokens import ConfirmTokenError
from app.core.request_context import (
    current_history,
    current_session_id,
    direct_user_commit,
)
from app.core.write_throttle import WriteThrottleError
from app.config import settings

logger = logging.getLogger(__name__)

_IDEMPOTENCY_TTL_SECONDS = 300
_MAX_BACKEND_ERROR_LEN = 240


class WriteToolError(Exception):
    """User-safe error wrapping any failure path of a write tool."""


def _short_backend_error(status_code: int, body: Any) -> str:
    """Mensagem curta para o usuario; detalhe completo so no log."""
    if isinstance(body, dict):
        msg = body.get("message") or body.get("error") or body.get("detail")
        text = str(msg) if msg else ""
    else:
        text = str(body or "")
    text = text.strip()
    if len(text) > _MAX_BACKEND_ERROR_LEN:
        text = text[:_MAX_BACKEND_ERROR_LEN].rstrip() + "..."
    if not text:
        text = "sem detalhes"
    return f"Operacao recusada pelo sistema (HTTP {status_code}): {text}"


def require_auth(bearer_token: str | None) -> None:
    """Exige Bearer em prod. ALLOW_ANONYMOUS_WRITES=true so e aceito em dev local."""
    if bearer_token:
        return
    if settings.allow_anonymous_writes:
        logger.warning(
            "Write sem Bearer — permitido por ALLOW_ANONYMOUS_WRITES (somente dev)."
        )
        return
    raise WriteToolError(
        "Acao recusada: e necessario estar autenticado para escrever no sistema."
    )


def effective_confirmed(args: dict) -> bool:
    """Commit so com confirm_token valido; evita confirmed=True na previa pelo modelo."""
    token = (args.get("confirm_token") or "").strip()
    if not token:
        return False
    return bool(args.get("confirmed", False))


def coerce_positive_int(value: Any, field: str) -> int:
    """Aceita int, float inteiro (ex. Gemini 5.0) e string numerica."""
    if value is None:
        raise WriteToolError(f"{field} e obrigatorio.")
    if isinstance(value, bool):
        raise WriteToolError(f"{field} deve ser um inteiro positivo.")
    if isinstance(value, float):
        if value <= 0 or value != int(value):
            raise WriteToolError(f"{field} deve ser um inteiro positivo.")
        return int(value)
    if isinstance(value, int):
        if value <= 0:
            raise WriteToolError(f"{field} deve ser um inteiro positivo.")
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return coerce_positive_int(int(value.strip()), field)
    raise WriteToolError(f"{field} deve ser um inteiro positivo.")


def issue_preview(tool_name: str, args: dict) -> str:
    session_id = current_session_id.get()
    history = current_history.get()
    user_count = confirm_tokens.count_user_messages(history)
    token = confirm_tokens.issue(
        tool_name=tool_name,
        args=args,
        session_id=session_id,
        user_msgs_at_issue=user_count,
    )
    return token.token


def verify_commit(tool_name: str, args: dict, confirm_token: str) -> None:
    session_id = current_session_id.get()
    history = current_history.get()
    try:
        confirm_tokens.verify_and_consume(
            token=confirm_token,
            tool_name=tool_name,
            args=args,
            session_id=session_id,
            current_history=history,
            enforce_user_turn=not direct_user_commit.get(),
        )
    except ConfirmTokenError as exc:
        raise WriteToolError(str(exc)) from exc


def get_idempotent_result(confirm_token: str) -> dict | None:
    """Retry com o mesmo token devolve o resultado anterior em vez de duplicar."""
    if not confirm_token:
        return None
    cached = cache.get(f"write_commit:{confirm_token}")
    return cached if isinstance(cached, dict) else None


def store_idempotent_result(confirm_token: str, result: dict) -> None:
    if confirm_token and isinstance(result, dict) and result.get("ok"):
        cache.set(f"write_commit:{confirm_token}", result, ttl=_IDEMPOTENCY_TTL_SECONDS)


async def run_idempotent_commit(confirm_token: str, commit_coro):
    """Run commit coroutine once; cache successful ok=True responses."""
    cached = get_idempotent_result(confirm_token)
    if cached is not None:
        return cached
    result = await commit_coro()
    store_idempotent_result(confirm_token, result)
    return result


def check_throttle(tool_name: str) -> None:
    session_id = current_session_id.get()
    try:
        # Re-fetch the throttle from its module each call so tests that
        # swap in a fresh limiter (or production swaps for a Redis-backed
        # implementation) are picked up without restarting the process.
        wt_module.write_throttle.check(session_id, tool_name)
    except WriteThrottleError as exc:
        raise WriteToolError(str(exc)) from exc


def preview_response(
    tool_name: str,
    confirm_token: str,
    payload: dict[str, Any],
    message: str,
) -> dict[str, Any]:
    return {
        "requires_confirmation": True,
        "action": tool_name,
        "confirm_token": confirm_token,
        "payload": payload,
        "message": message,
    }


def _auth_headers(bearer_token: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}


def _safe_body(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return resp.text


async def get_json(
    client: httpx.AsyncClient,
    url: str,
    bearer_token: str | None,
) -> dict | list:
    resp = await client.get(url, headers=_auth_headers(bearer_token))
    if resp.status_code >= 400:
        body = _safe_body(resp)
        logger.warning("GET %s falhou (%s): %s", url, resp.status_code, body)
        raise WriteToolError(_short_backend_error(resp.status_code, body))
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return {"raw": resp.text}


async def post_json(
    client: httpx.AsyncClient,
    url: str,
    json_body: dict,
    bearer_token: str | None,
) -> dict:
    resp = await client.post(url, json=json_body, headers=_auth_headers(bearer_token))
    if resp.status_code >= 400:
        body = _safe_body(resp)
        logger.warning("POST %s falhou (%s): %s", url, resp.status_code, body)
        raise WriteToolError(_short_backend_error(resp.status_code, body))
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return {"raw": resp.text}


async def delete_request(
    client: httpx.AsyncClient,
    url: str,
    bearer_token: str | None,
) -> None:
    resp = await client.delete(url, headers=_auth_headers(bearer_token))
    if resp.status_code == 404:
        raise WriteToolError("Recurso nao encontrado.")
    if resp.status_code >= 400:
        body = _safe_body(resp)
        logger.warning("DELETE %s falhou (%s): %s", url, resp.status_code, body)
        raise WriteToolError(_short_backend_error(resp.status_code, body))


async def delete_best_effort(
    client: httpx.AsyncClient,
    url: str,
    bearer_token: str | None,
) -> None:
    """Rollback de cadeia parcial: DELETE silencioso para nao mascarar o erro original."""
    try:
        await client.delete(url, headers=_auth_headers(bearer_token))
    except Exception:  # noqa: BLE001
        pass
