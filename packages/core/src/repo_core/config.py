from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    app_secret_key: str = "change-me"
    api_base_url: str = "http://localhost:8001"
    web_base_url: str = "http://localhost:3000"
    cors_origins: str = "http://localhost:3000"

    database_url: str = "postgresql+asyncpg://repo:repo@localhost:5433/repo_understanding"
    database_url_sync: str = "postgresql+psycopg://repo:repo@localhost:5433/repo_understanding"

    redis_url: str = "redis://localhost:6379/0"

    clone_root: Path = Path("./data/clones")
    clone_depth: int = 1

    github_app_id: str = ""
    github_app_client_id: str = ""
    github_app_client_secret: str = ""
    github_app_private_key_path: Path = Path("./private-key.pem")
    github_app_webhook_secret: str = ""
    github_app_slug: str = ""

    session_cookie_name: str = "repo_session"
    session_max_age_seconds: int = 60 * 60 * 24 * 7

    llm_provider: str = ""
    llm_api_key: str = ""
    llm_model: str = ""

    # Phase 2 embeddings — empty provider defaults to deterministic mock (local/dev).
    embedding_provider: str = ""
    embedding_api_key: str = ""
    embedding_model: str = "voyage-code-3"
    embedding_dims: int = 1024

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def github_configured(self) -> bool:
        return bool(
            self.github_app_id
            and self.github_app_client_id
            and self.github_app_client_secret
            and self.github_app_private_key_path.exists()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
