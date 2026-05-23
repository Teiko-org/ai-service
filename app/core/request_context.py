"""Per-request context for write tools.

Write tools need the active session id (for HMAC binding) and the
conversation history (to enforce "new user turn between preview and commit").
Threading this through the existing tool executor signature would be
invasive, so we publish them via ContextVar and let writes read them on
demand.
"""

from __future__ import annotations

from contextvars import ContextVar

current_session_id: ContextVar[str | None] = ContextVar(
    "current_session_id", default=None
)
current_history: ContextVar[list[dict] | None] = ContextVar(
    "current_history", default=None
)
# True quando o commit vem do app/botao ou do "sim" interceptado (nao do Gemini).
direct_user_commit: ContextVar[bool] = ContextVar("direct_user_commit", default=False)
