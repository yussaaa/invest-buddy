"""Application configuration via pydantic-settings.

All settings are read from environment variables (or a .env file).
See .env.example for the full list of supported variables.
"""

from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


ProviderName = Literal["openai", "anthropic", "qwen", "ollama", "vllm"]
PROVIDER_NAMES: tuple[str, ...] = ("openai", "anthropic", "qwen", "ollama", "vllm")

# The provider a request asked for (Settings page → X-LLM-Provider header →
# app/models/selection.py). Unset means MODEL_PROVIDER from .env. A ContextVar
# rather than a mutated setting because two browsers can choose differently,
# and the analysis fan-out's tasks inherit it from the request that started them.
requested_provider: ContextVar[Optional[str]] = ContextVar("requested_provider", default=None)


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
    # Thinking depth for models that always think (Opus 4.7+, the 5.x line).
    # Empty means the model's own default; ignored by the 4.6-and-older models.
    anthropic_effort: Literal["", "low", "medium", "high", "xhigh", "max"] = ""

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

    # ── Volatility history ──────────────────────────────────────────────────
    # Yahoo publishes no implied-volatility history, so the default provider
    # can only compare today's IV against realized vol. A vendor with dated
    # historical chains gives a real 52-week IV rank; see services/vol_provider.
    vol_data_provider: Literal["realized_proxy", "marketdata"] = "realized_proxy"
    marketdata_api_key: str = ""

    # ── Social sentiment (services/social/) ────────────────────────────────
    # ApeWisdom (Reddit mention counts) and StockTwits (self-tagged bull/bear)
    # need no key. StockTwits sits behind Cloudflare and routinely refuses
    # server clients; a breaker turns that into a caveat rather than a delay.
    social_enabled: bool = True
    stocktwits_enabled: bool = True
    social_http_timeout_seconds: float = 6.0
    social_user_agent: str = "agent-invest/0.17 (+https://github.com/yussaaa/agent_invest)"
    # Hourly ApeWisdom snapshot into Postgres — the history a mention z-score
    # needs, since the API only reports "24 hours ago".
    social_snapshot_enabled: bool = True
    # Placeholder providers: declared so the UI can say what is missing.
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = ""
    x_bearer_token: str = ""

    # ── Research agent (agents/research/) ──────────────────────────────────
    research_enabled: bool = True
    research_model: Literal["fast", "smart"] = "fast"
    research_max_tool_iterations: int = 2
    research_max_tool_calls: int = 4
    research_max_web_searches: int = 3
    research_timeout_seconds: float = 60.0

    # ── Background warm-up (services/warmup.py) ────────────────────────────
    # Backfills the bar store and refreshes the earnings calendar ahead of
    # expiry, so the first visitor after a cold start or a TTL never waits.
    warmup_enabled: bool = True

    # ── Chat agent ──────────────────────────────────────────────────────────
    chat_enabled: bool = True
    # The reasoning is deterministic (services/signals.py), so the model only
    # narrates and picks tools — work the fast model handles at a fraction of
    # the cost and latency. Set to "smart" if tool selection proves unreliable.
    chat_model: Literal["fast", "smart"] = "fast"
    chat_max_tool_iterations: int = 3
    chat_max_tool_calls: int = 4
    chat_history_turns: int = 12
    # Ollama and vLLM expose tool-calling only for some models, and the small
    # local Qwens are unreliable at it. Off means the agent answers from the
    # pre-fetched signals in one pass rather than failing.
    chat_tools_enabled: bool = True
    # Start streaming the answer alongside the first gather call, and keep it
    # when gather asks for no tool (most turns). Saves gather's round trip on
    # time-to-first-token; costs a few discarded tokens on tool turns.
    chat_speculative_answer: bool = True

    # ── Monitoring ──────────────────────────────────────────────────────────
    monitoring_backend: Literal["mlflow", "wandb", "none"] = "mlflow"
    mlflow_tracking_uri: str = "http://localhost:5050"
    wandb_api_key: str = ""
    wandb_project: str = "agent-invest"

    # ── Derived helpers ─────────────────────────────────────────────────────
    @property
    def active_provider(self) -> str:
        """The provider for this request: the user's choice, else MODEL_PROVIDER."""
        return requested_provider.get() or self.model_provider

    def models_for(self, provider: str) -> tuple[str, str]:
        """(fast, smart) model names for a provider."""
        return (getattr(self, f"{provider}_fast_model"), getattr(self, f"{provider}_smart_model"))

    @property
    def fast_model(self) -> str:
        """Return the fast (cheap) model name for the active provider."""
        return self.models_for(self.active_provider)[0]

    @property
    def smart_model(self) -> str:
        """Return the smart (reasoning) model name for the active provider."""
        return self.models_for(self.active_provider)[1]

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",")]


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton — call this everywhere."""
    return Settings()
