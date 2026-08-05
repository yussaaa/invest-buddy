"""Application configuration via pydantic-settings.

All settings are read from environment variables (or a .env file).
See .env.example for the full list of supported variables.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_env_file() -> Path | None:
    """Walk up from this file's location until we find a .env file.

    Supports running uvicorn from backend/, the project root, or Docker
    (where .env is bind-mounted at /app/.env).
    """
    here = Path(__file__).resolve().parent
    for directory in [here, *here.parents]:
        candidate = directory / ".env"
        if candidate.exists():
            return candidate
    return None


_ENV_FILE = _find_env_file()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE) if _ENV_FILE else ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ────────────────────────────────────────────────────────────────
    app_env: Literal["development", "production"] = "development"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173"
    secret_key: str = "change-me-in-production"

    # ── Model Provider ──────────────────────────────────────────────────────
    model_provider: Literal["openai", "anthropic", "qwen", "ollama", "vllm"] = "openai"

    # OpenAI
    openai_api_key: str = ""
    openai_fast_model: str = "gpt-4o-mini"
    openai_smart_model: str = "gpt-4o"

    # Anthropic
    anthropic_api_key: str = ""
    anthropic_fast_model: str = "claude-haiku-4-5"
    anthropic_smart_model: str = "claude-sonnet-4-6"

    # Qwen (DashScope)
    dashscope_api_key: str = ""
    qwen_fast_model: str = "qwen2.5-7b-instruct"
    qwen_smart_model: str = "qwen2.5-72b-instruct"

    # Ollama (local)
    ollama_base_url: str = "http://localhost:11434"
    ollama_fast_model: str = "qwen2.5:7b"
    ollama_smart_model: str = "qwen2.5:14b"

    # vLLM (local GPU)
    vllm_base_url: str = "http://localhost:8000"
    vllm_fast_model: str = "Qwen2.5-7B-Instruct"
    vllm_smart_model: str = "Qwen2.5-72B-Instruct"

    # ── Database ────────────────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://agent_invest:password@localhost:5432/agent_invest"

    # ── Redis ───────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── RAG / Embeddings ─────────────────────────────────────────────────────
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_provider: Literal["local", "openai"] = "local"
    reranker_model: str = "BAAI/bge-reranker-base"
    rag_chunk_size: int = 512        # tokens per chunk
    rag_chunk_overlap: int = 50      # token overlap between chunks
    rag_top_k: int = 8               # final retrieved chunks

    # ── External APIs ───────────────────────────────────────────────────────
    newsapi_key: str = ""
    tavily_api_key: str = ""
    alpha_vantage_key: str = ""
    fred_api_key: str = ""    # optional — powers the economic release calendar

    # ── Monitoring ──────────────────────────────────────────────────────────
    monitoring_backend: Literal["mlflow", "wandb", "none"] = "mlflow"
    mlflow_tracking_uri: str = "http://localhost:5050"
    wandb_api_key: str = ""
    wandb_project: str = "agent-invest"

    # ── Derived helpers ─────────────────────────────────────────────────────
    @property
    def fast_model(self) -> str:
        """Return the fast (cheap) model name for the active provider."""
        mapping = {
            "openai": self.openai_fast_model,
            "anthropic": self.anthropic_fast_model,
            "qwen": self.qwen_fast_model,
            "ollama": self.ollama_fast_model,
            "vllm": self.vllm_fast_model,
        }
        return mapping[self.model_provider]

    @property
    def smart_model(self) -> str:
        """Return the smart (reasoning) model name for the active provider."""
        mapping = {
            "openai": self.openai_smart_model,
            "anthropic": self.anthropic_smart_model,
            "qwen": self.qwen_smart_model,
            "ollama": self.ollama_smart_model,
            "vllm": self.vllm_smart_model,
        }
        return mapping[self.model_provider]

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",")]


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton — call this everywhere."""
    return Settings()
