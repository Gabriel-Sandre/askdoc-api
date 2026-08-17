"""Configuração central da aplicação, lida de variáveis de ambiente / .env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Banco
    database_url: str = "sqlite+aiosqlite:///./askdoc.db"

    # Providers
    llm_provider: str = "fake"
    embedding_provider: str = "fake"
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    openai_chat_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    anthropic_chat_model: str = "claude-sonnet-4-5"
    embedding_dim: int = 256

    # Retrieval
    use_pgvector: bool = False
    chunk_size: int = 900
    chunk_overlap: int = 150
    retrieval_top_k: int = 5
    candidate_pool: int = 40

    # API
    admin_token: str = "dev-admin-token"
    rate_limit_per_minute: int = 60
    max_upload_bytes: int = 5 * 1024 * 1024
    log_level: str = "INFO"

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")

    @property
    def pgvector_enabled(self) -> bool:
        """pgvector só faz sentido em cima do Postgres."""
        return self.use_pgvector and self.is_postgres


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
