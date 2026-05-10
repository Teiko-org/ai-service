import time
import threading
import logging
from app.config import settings, FALLBACK_MODELS

logger = logging.getLogger(__name__)

COOLDOWN_SECONDS = 65


class ModelManager:
    """Tracks rate-limited models and returns the best available one."""

    def __init__(self):
        self._cooldowns: dict[str, float] = {}
        self._lock = threading.Lock()

        primary = settings.gemini_model
        if primary in FALLBACK_MODELS:
            self._models = [primary] + [m for m in FALLBACK_MODELS if m != primary]
        else:
            self._models = [primary] + FALLBACK_MODELS

    def get_model(self) -> str:
        with self._lock:
            now = time.time()
            for model in self._models:
                expires = self._cooldowns.get(model, 0)
                if now >= expires:
                    return model

        logger.warning("Todos os modelos em cooldown, usando o primario mesmo assim")
        return self._models[0]

    def mark_rate_limited(self, model: str, retry_after: float | None = None) -> str | None:
        cooldown = retry_after if retry_after and retry_after > 0 else COOLDOWN_SECONDS

        with self._lock:
            self._cooldowns[model] = time.time() + cooldown
            logger.warning(
                "Modelo %s rate-limited por %.0fs, tentando fallback...",
                model, cooldown,
            )

            now = time.time()
            for fallback in self._models:
                if fallback == model:
                    continue
                expires = self._cooldowns.get(fallback, 0)
                if now >= expires:
                    logger.info("Fallback para modelo: %s", fallback)
                    return fallback

            others = [
                (m, self._cooldowns.get(m, 0.0))
                for m in self._models
                if m != model
            ]
            if not others:
                if len(self._models) == 1:
                    logger.warning(
                        "Apenas um modelo configurado; reduzindo cooldown para nova tentativa"
                    )
                    self._cooldowns[model] = now + min(cooldown, 12.0)
                    return model
                logger.error("Lista de modelos invalida para fallback")
                return None

            best_model, best_expires = min(others, key=lambda x: x[1])
            wait = max(0.0, best_expires - now)
            logger.warning(
                "Todos em cooldown; liberando %s (expirava em %.0fs) para tentar de novo",
                best_model,
                wait,
            )
            self._cooldowns[best_model] = 0.0
            return best_model

    def get_status(self) -> dict:
        now = time.time()
        with self._lock:
            return {
                model: {
                    "available": now >= self._cooldowns.get(model, 0),
                    "cooldown_remaining": max(0, self._cooldowns.get(model, 0) - now),
                }
                for model in self._models
            }


model_manager = ModelManager()
