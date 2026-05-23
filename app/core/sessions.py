import uuid
import time
import threading
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

SESSION_TTL_SECONDS = 30 * 60  # 30 minutes
CLEANUP_INTERVAL_SECONDS = 5 * 60  # run cleanup every 5 minutes
MAX_HISTORY_PER_SESSION = 50


@dataclass
class Session:
    id: str
    history: list[dict] = field(default_factory=list)
    pending_confirmation: dict | None = None
    last_fornada_id: int | None = None
    last_pedido_resumo_id: int | None = None
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)

    def touch(self):
        self.last_active = time.time()

    def is_expired(self) -> bool:
        return (time.time() - self.last_active) > SESSION_TTL_SECONDS


class SessionStore:

    def __init__(self):
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()
        self._start_cleanup_timer()

    def get_or_create(self, session_id: str | None = None) -> Session:
        with self._lock:
            if session_id and session_id in self._sessions:
                session = self._sessions[session_id]
                if not session.is_expired():
                    session.touch()
                    return session
                del self._sessions[session_id]

            new_id = session_id or str(uuid.uuid4())
            session = Session(id=new_id)
            self._sessions[new_id] = session
            logger.info("Nova sessao criada: %s", new_id)
            return session

    def append(self, session_id: str, role: str, content: str) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return
            session.history.append({"role": role, "content": content})
            if len(session.history) > MAX_HISTORY_PER_SESSION:
                session.history = session.history[-MAX_HISTORY_PER_SESSION:]
            session.touch()

    def get_history(self, session_id: str, limit: int = 10) -> list[dict]:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return []
            return session.history[-limit:]

    def set_pending_confirmation(
        self, session_id: str, pending: dict | None
    ) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return
            session.pending_confirmation = pending
            session.touch()

    def get_pending_confirmation(self, session_id: str) -> dict | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return None
            return session.pending_confirmation

    def set_last_fornada_id(self, session_id: str, fornada_id: int | None) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return
            session.last_fornada_id = fornada_id
            session.touch()

    def get_last_fornada_id(self, session_id: str) -> int | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return None
            return session.last_fornada_id

    def set_last_pedido_resumo_id(
        self, session_id: str, pedido_resumo_id: int | None
    ) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return
            session.last_pedido_resumo_id = pedido_resumo_id
            session.touch()

    def get_last_pedido_resumo_id(self, session_id: str) -> int | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return None
            return session.last_pedido_resumo_id

    def _cleanup(self) -> None:
        with self._lock:
            expired = [
                sid for sid, s in self._sessions.items() if s.is_expired()
            ]
            for sid in expired:
                del self._sessions[sid]
            if expired:
                logger.info("Sessoes expiradas removidas: %d", len(expired))
        self._start_cleanup_timer()

    def _start_cleanup_timer(self) -> None:
        timer = threading.Timer(CLEANUP_INTERVAL_SECONDS, self._cleanup)
        timer.daemon = True
        timer.start()


session_store = SessionStore()
