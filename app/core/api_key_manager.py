import logging
import threading
import time

from app.config import settings

logger = logging.getLogger(__name__)

COOLDOWN_SECONDS = 65


class ApiKeyManager:
    """Tracks rate-limited API keys and returns the best available index."""

    def __init__(self):
        self._cooldowns: dict[int, float] = {}
        self._lock = threading.Lock()
        self._key_count = len(settings.gemini_api_keys_list)

    def get_key_index(self) -> int:
        with self._lock:
            now = time.time()
            for idx in range(self._key_count):
                expires = self._cooldowns.get(idx, 0)
                if now >= expires:
                    return idx

        logger.warning("Todas as API keys em cooldown, usando a primeira mesmo assim")
        return 0

    def mark_rate_limited(
        self, key_index: int, retry_after: float | None = None
    ) -> int | None:
        cooldown = retry_after if retry_after and retry_after > 0 else COOLDOWN_SECONDS

        with self._lock:
            self._cooldowns[key_index] = time.time() + cooldown
            logger.warning(
                "API key #%d rate-limited por %.0fs, tentando proxima chave...",
                key_index + 1,
                cooldown,
            )

            now = time.time()
            for idx in range(self._key_count):
                if idx == key_index:
                    continue
                expires = self._cooldowns.get(idx, 0)
                if now >= expires:
                    logger.info("Fallback para API key #%d", idx + 1)
                    return idx

            others = [
                (i, self._cooldowns.get(i, 0.0))
                for i in range(self._key_count)
                if i != key_index
            ]
            if not others:
                if self._key_count == 1:
                    logger.warning(
                        "Apenas uma API key configurada; reduzindo cooldown para nova tentativa"
                    )
                    self._cooldowns[key_index] = now + min(cooldown, 12.0)
                    return key_index
                logger.error("Lista de API keys invalida para fallback")
                return None

            best_idx, best_expires = min(others, key=lambda x: x[1])
            wait = max(0.0, best_expires - now)
            logger.warning(
                "Todas as keys em cooldown; liberando key #%d (expirava em %.0fs)",
                best_idx + 1,
                wait,
            )
            self._cooldowns[best_idx] = 0.0
            return best_idx

    def get_status(self) -> dict:
        now = time.time()
        with self._lock:
            return {
                f"key_{idx + 1}": {
                    "available": now >= self._cooldowns.get(idx, 0),
                    "cooldown_remaining": max(
                        0, self._cooldowns.get(idx, 0) - now
                    ),
                }
                for idx in range(self._key_count)
            }


api_key_manager = ApiKeyManager()
