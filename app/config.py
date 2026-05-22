from pydantic_settings import BaseSettings


FALLBACK_MODELS = [
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
]


class Settings(BaseSettings):
    gemini_api_key: str
    gemini_model: str = "gemini-2.5-flash-lite"
    carambolos_api_url: str = "http://localhost:8080"
    allowed_origins: str = "http://localhost:8081,http://localhost:19006"
    log_level: str = "INFO"

    enable_write_tools: bool = False
    confirm_token_secret: str = ""
    confirm_token_ttl_seconds: int = 120

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",")]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()


if settings.enable_write_tools and not settings.confirm_token_secret:
    raise RuntimeError(
        "ENABLE_WRITE_TOOLS=true exige CONFIRM_TOKEN_SECRET configurado no .env."
    )
