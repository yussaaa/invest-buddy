"""Whether the configured model provider can actually be called.

Extracted from technicals.py so that every on-demand explainer can ask the
same question the same way. The answer decides whether a panel offers an
"explain this" button or a sentence saying which key is missing — either is
fine, silently failing the request is not.
"""

from __future__ import annotations

from app.config import Settings


def has_llm_credentials(settings: Settings) -> bool:
    """Local providers need no key; hosted ones do."""
    provider = settings.model_provider
    if provider in ("ollama", "vllm"):
        return True
    return bool(
        {
            "openai": settings.openai_api_key,
            "anthropic": settings.anthropic_api_key,
            "qwen": settings.dashscope_api_key,
        }.get(provider)
    )


def missing_credentials_reason(settings: Settings) -> str:
    """A message that names the fix rather than just the failure."""
    return (
        f"No credentials configured for MODEL_PROVIDER={settings.model_provider}. "
        "Set the provider's API key in .env, or use MODEL_PROVIDER=ollama to run locally."
    )
