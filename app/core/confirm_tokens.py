"""HMAC-signed preview tokens for write tools (V3).

Two-step confirmation flow:

  1. Tool called with `confirmed=False`. Executor issues a short-lived HMAC
     token bound to (tool_name, args, session_id) and returns it in the
     preview.
  2. Tool called with `confirmed=True` + `confirm_token`. Executor verifies
     the HMAC, expiration, anti-replay and that a new user message arrived
     between preview and commit.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
from dataclasses import dataclass

from app.config import settings

logger = logging.getLogger(__name__)


class ConfirmTokenError(Exception):
    """User-safe validation error for preview tokens."""


@dataclass(frozen=True)
class PreviewToken:
    token: str
    issued_at: float
    expires_at: float


def _canonicalize(tool_name: str, args: dict, session_id: str | None) -> bytes:
    # Strip flow-control fields so the same token issued on the preview
    # (confirmed=False) still validates the commit (confirmed=True + token).
    payload_args = {
        k: v for k, v in args.items() if k not in {"confirmed", "confirm_token"}
    }
    payload = {
        "tool": tool_name,
        "args": payload_args,
        "session_id": session_id or "",
    }
    # sort_keys: stable hashing regardless of dict insertion order.
    return json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")


def _hmac_hex(data: bytes) -> str:
    secret = settings.confirm_token_secret.encode("utf-8")
    return hmac.new(secret, data, hashlib.sha256).hexdigest()


def issue(
    tool_name: str,
    args: dict,
    session_id: str | None,
    ttl_seconds: int | None = None,
) -> PreviewToken:
    if not settings.confirm_token_secret:
        raise ConfirmTokenError(
            "Servidor sem CONFIRM_TOKEN_SECRET configurado para writes."
        )
    ttl = ttl_seconds or settings.confirm_token_ttl_seconds
    issued = time.time()
    expires = issued + max(1, ttl)
    canon = _canonicalize(tool_name, args, session_id)
    body = f"{int(expires)}.".encode("utf-8") + canon
    digest = _hmac_hex(body)
    token = f"{int(expires)}.{digest}"
    return PreviewToken(token=token, issued_at=issued, expires_at=expires)


class _ConsumedTokensStore:
    # Anti-replay: once a token is used to commit, any retry (network or
    # model loop) must be rejected. In-memory store is fine for the current
    # single-process deployment; move to Redis if we ever scale horizontally.
    def __init__(self) -> None:
        self._consumed: dict[str, float] = {}
        self._lock = threading.Lock()

    def is_consumed(self, token: str) -> bool:
        with self._lock:
            self._evict_expired()
            return token in self._consumed

    def mark_consumed(self, token: str, expires_at: float) -> None:
        with self._lock:
            self._consumed[token] = expires_at

    def _evict_expired(self) -> None:
        now = time.time()
        expired = [t for t, exp in self._consumed.items() if exp < now]
        for t in expired:
            del self._consumed[t]


_consumed_store = _ConsumedTokensStore()


def _parse_token(token: str) -> tuple[int, str]:
    try:
        exp_str, digest = token.split(".", 1)
        return int(exp_str), digest
    except (ValueError, AttributeError) as exc:
        raise ConfirmTokenError("Token de confirmacao invalido.") from exc


def verify_and_consume(
    token: str,
    tool_name: str,
    args: dict,
    session_id: str | None,
) -> None:
    if not token:
        raise ConfirmTokenError(
            "Faltou o token de confirmacao. Pec,a confirmacao antes."
        )

    expires_at, digest = _parse_token(token)

    if expires_at < time.time():
        raise ConfirmTokenError(
            "A confirmacao expirou. Pec,a a previa de novo antes de confirmar."
        )

    canon = _canonicalize(tool_name, args, session_id)
    body = f"{expires_at}.".encode("utf-8") + canon
    expected = _hmac_hex(body)

    # compare_digest avoids timing attacks on the HMAC check.
    if not hmac.compare_digest(digest, expected):
        raise ConfirmTokenError(
            "Os dados confirmados nao batem com a previa. Refaca a previa."
        )

    if _consumed_store.is_consumed(token):
        raise ConfirmTokenError(
            "Essa confirmacao ja foi processada. Nao vou repetir a acao."
        )

    _consumed_store.mark_consumed(token, expires_at)


def count_user_messages(history: list[dict] | None) -> int:
    if not history:
        return 0
    return sum(1 for m in history if m.get("role") == "user")


def ensure_user_turn_between(
    user_msgs_at_issue: int,
    current_history: list[dict] | None,
) -> None:
    # Defense against the model emitting preview and commit in the same
    # turn (V15). A real human confirmation must show up as a new user
    # message between the two tool calls.
    current = count_user_messages(current_history)
    if current <= user_msgs_at_issue:
        raise ConfirmTokenError(
            "Confirmacao recusada: e necessaria uma resposta explicita do "
            "usuario depois da previa."
        )
