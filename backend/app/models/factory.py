"""ModelProvider factory — one client per provider, chosen per request."""

from functools import lru_cache

from app.config import Settings, get_settings
from app.models.provider import (
    AnthropicProvider,
    ModelProvider,
    OllamaProvider,
    OpenAIProvider,
    QwenProvider,
    VLLMProvider,
)


@lru_cache
def _client_for(name: str) -> ModelProvider:
    """One long-lived client per provider, so switching never rebuilds a pool."""
    cfg = get_settings()
    match name:
        case "openai":
            return OpenAIProvider(api_key=cfg.openai_api_key)
        case "anthropic":
            return AnthropicProvider(api_key=cfg.anthropic_api_key, effort=cfg.anthropic_effort)
        case "qwen":
            return QwenProvider(api_key=cfg.dashscope_api_key)
        case "ollama":
            return OllamaProvider(base_url=cfg.ollama_base_url)
        case "vllm":
            return VLLMProvider(base_url=cfg.vllm_base_url)
        case _:
            raise ValueError(f"Unknown model provider: {name}")


def get_provider(settings: Settings | None = None) -> ModelProvider:
    """The ModelProvider for this request.

    That is the provider the user picked on the Settings page (carried by the
    X-LLM-Provider header, see app/models/selection.py), else MODEL_PROVIDER.
    Call it where the model is used, not once at construction: the specialist
    agents are module-level singletons and would otherwise pin the provider
    that happened to be active at import.

    Interview talking point: swapping from OpenAI to Ollama for zero-cost
    local dev is one env var change — MODEL_PROVIDER=ollama — or one click.
    """
    cfg = settings or get_settings()
    return _client_for(cfg.active_provider)
