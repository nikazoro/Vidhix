from functools import lru_cache
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR.parent / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # LLM
    llm_provider: str = Field(default="ollama")
    ollama_base_url: str = Field(default="http://localhost:11434")
    ollama_model: str = Field(default="phi3:mini")
    gemini_api_key: str = Field(default="")
    gemini_model: str = Field(default="gemini-1.5-flash")

    # Embeddings
    embedding_model: str = Field(default="nomic-embed-text")
    embedding_base_url: str = Field(default="http://localhost:11434")

    # Qdrant
    qdrant_url: str = Field(default="http://localhost:6333")
    qdrant_api_key: str = Field(default="")

    # PostgreSQL
    database_url: str = Field(
        default="postgresql+asyncpg://lexara:lexara123@localhost:5432/lexara_db"
    )

    # Redis
    redis_url: str = Field(default="redis://localhost:6379/0")

    # Langfuse
    langfuse_public_key: str = Field(default="")
    langfuse_secret_key: str = Field(default="")
    langfuse_host: str = Field(default="https://cloud.langfuse.com")

    # App
    app_env: str = Field(default="development")
    secret_key: str = Field(default="change_this_to_a_random_64_char_string")
    max_upload_size_mb: int = Field(default=20)
    session_ttl_hours: int = Field(default=24)

    # Storage
    upload_dir: str = Field(default="uploads")
    cloudflare_r2_endpoint: str = Field(default="")
    cloudflare_r2_access_key_id: str = Field(default="")
    cloudflare_r2_secret_access_key: str = Field(default="")
    cloudflare_r2_bucket: str = Field(default="")

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def llm_model_name(self) -> str:
        if self.llm_provider == "gemini":
            return self.gemini_model
        return self.ollama_model

    @property
    def langfuse_enabled(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)

    @property
    def upload_path(self) -> Path:
        p = BASE_DIR / self.upload_dir
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def bm25_index_path(self) -> Path:
        p = BASE_DIR / "data" / "bm25_index.pkl"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def workflows_path(self) -> Path:
        return BASE_DIR / "data" / "workflows"

    @property
    def escalation_graph_path(self) -> Path:
        return BASE_DIR / "data" / "escalation" / "escalation_graph.json"

    @property
    def corpus_path(self) -> Path:
        return BASE_DIR / "data" / "corpus"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()