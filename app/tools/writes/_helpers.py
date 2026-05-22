"""Shared helpers for write tools (V3 phase 1+).

Every write tool follows the same two-step shape:

    if not confirmed:
        validate_args(...)
        token = issue_preview_token(name, args)
        return preview_response(...)

    require_auth(token_bearer)
    require_throttle(name)
    verify_commit_token(name, args, confirm_token)
    do_post(...)

This module centralizes the boilerplate so each tool only encodes its
domain logic (validation + endpoint + payload).
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core import confirm_tokens, write_throttle as wt_module
from app.core.cache import cache
from app.core.confirm_tokens import ConfirmTokenError
from app.core.request_context import current_history, current_session_id
from app.core.write_throttle import WriteThrottleError

_IDEMPOTENCY_TTL_SECONDS = 300


class WriteToolError(Exception):
    """User-safe error wrapping any failure path of a write tool."""


def require_auth(bearer_token: str | None) -> None:
    # G5: writes must never reach the backend anonymously, even if the
    # backend itself is lax about it. Refuse early with a clear message.
    if not bearer_token:
        raise WriteToolError(
            "Acao recusada: e necessario estar autenticado para escrever no sistema."
        )


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
        )
    except ConfirmTokenError as exc:
        raise WriteToolError(str(exc)) from exc


def get_idempotent_result(confirm_token: str) -> dict | None:
    # G2: network retry with the same token after a successful commit returns
    # the cached outcome instead of duplicating the write.
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


async def get_json(
    client: httpx.AsyncClient,
    url: str,
    bearer_token: str | None,
) -> dict | list:
    resp = await client.get(url, headers=_auth_headers(bearer_token))
    if resp.status_code >= 400:
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            body = resp.text
        raise WriteToolError(
            f"Backend recusou a consulta (HTTP {resp.status_code}): {body}"
        )
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
        # Surface backend validation errors verbatim so the model can
        # explain them to the user instead of swallowing the cause.
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            body = resp.text
        raise WriteToolError(
            f"Backend recusou a operacao (HTTP {resp.status_code}): {body}"
        )
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return {"raw": resp.text}


async def delete_best_effort(
    client: httpx.AsyncClient,
    url: str,
    bearer_token: str | None,
) -> None:
    # Rollback after a partial write chain: best-effort DELETEs; failures are
    # swallowed so the original error still reaches the user/model.
    try:
        await client.delete(url, headers=_auth_headers(bearer_token))
    except Exception:  # noqa: BLE001
        pass
