import logging

from pydantic_settings import BaseSettings

from app.core.model_catalog import DEFAULT_GEMINI_FALLBACK_MODELS

# Retrocompat: testes e imports antigos.
FALLBACK_MODELS = list(DEFAULT_GEMINI_FALLBACK_MODELS)

_MIN_SECRET_LEN = 24

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    gemini_api_key: str = ""
    gemini_api_keys: str = ""
    gemini_model: str = "gemini-2.5-flash-lite"
    # Lista extra de modelos para fallback (virgula). Se vazio, usa DEFAULT_GEMINI_FALLBACK_MODELS.
    gemini_fallback_models: str = ""
    carambolos_api_url: str = "http://localhost:8080"
    allowed_origins: str = "http://localhost:8081,http://localhost:19006"
    log_level: str = "INFO"
    environment: str = "development"

    enable_write_tools: bool = False
    confirm_token_secret: str = ""
    confirm_token_ttl_seconds: int = 120
    allow_anonymous_writes: bool = False

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",")]

    @property
    def gemini_api_keys_list(self) -> list[str]:
        """Chaves Gemini: GEMINI_API_KEYS ou GEMINI_API_KEY (virgula = varias)."""
        if self.gemini_api_keys.strip():
            return self._split_keys(self.gemini_api_keys)
        if self.gemini_api_key.strip():
            return self._split_keys(self.gemini_api_key)
        return []

    @staticmethod
    def _split_keys(raw: str) -> list[str]:
        return [key.strip() for key in raw.split(",") if key.strip()]

    @property
    def fallback_models_list(self) -> list[str]:
        if self.gemini_fallback_models.strip():
            return self._split_keys(self.gemini_fallback_models)
        return list(DEFAULT_GEMINI_FALLBACK_MODELS)

    @property
    def model_chain(self) -> list[str]:
        """Ordem efetiva: GEMINI_MODEL + fallbacks (sem duplicar)."""
        from app.core.model_catalog import build_model_chain

        return build_model_chain(self.gemini_model, self.fallback_models_list)

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()

if not settings.gemini_api_keys_list:
    raise RuntimeError(
        "Configure GEMINI_API_KEY ou GEMINI_API_KEYS (chaves separadas por virgula) no .env."
    )

if settings.enable_write_tools and not settings.confirm_token_secret:
    raise RuntimeError(
        "ENABLE_WRITE_TOOLS=true exige CONFIRM_TOKEN_SECRET configurado no .env."
    )

if (
    settings.enable_write_tools
    and settings.confirm_token_secret
    and len(settings.confirm_token_secret) < _MIN_SECRET_LEN
):
    logger.warning(
        "CONFIRM_TOKEN_SECRET com menos de %d caracteres. "
        "Gere algo forte: python -c \"import secrets; print(secrets.token_urlsafe(48))\".",
        _MIN_SECRET_LEN,
    )

if settings.environment.lower() == "production" and settings.allow_anonymous_writes:
    raise RuntimeError(
        "ALLOW_ANONYMOUS_WRITES=true e proibido em ENVIRONMENT=production. "
        "Configure autenticacao no cliente antes de habilitar writes."
    )
