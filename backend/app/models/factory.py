"""ModelProvider factory — returns the configured provider singleton."""

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
def get_provider(settings: Settings | None = None) -> ModelProvider:
    """Return the configured ModelProvider (cached singleton).

    Interview talking point: swapping from OpenAI to Ollama for zero-cost
    local dev is one env var change — MODEL_PROVIDER=ollama.
    """
    cfg = settings or get_settings()

    match cfg.model_provider:
        case "openai":
            return OpenAIProvider(api_key=cfg.openai_api_key)
        case "anthropic":
            return AnthropicProvider(api_key=cfg.anthropic_api_key)
        case "qwen":
            return QwenProvider(api_key=cfg.dashscope_api_key)
        case "ollama":
            return OllamaProvider(base_url=cfg.ollama_base_url)
        case "vllm":
            return VLLMProvider(base_url=cfg.vllm_base_url)
        case _:
            raise ValueError(f"Unknown model provider: {cfg.model_provider}")
