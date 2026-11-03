"""Whether a model provider can actually be called.

Extracted from technicals.py so that every on-demand explainer can ask the
same question the same way. The answer decides whether a panel offers an
"explain this" button or a sentence saying which key is missing — either is
fine, silently failing the request is not.

The same check decides which providers the Settings page offers: a hosted
provider is selectable only when its key is in .env.
"""

from __future__ import annotations

from typing import Optional

from app.config import PROVIDER_NAMES, Settings

# Hosted providers and the setting that holds each one's key.
_KEY_SETTING = {
    "openai": "openai_api_key",
    "anthropic": "anthropic_api_key",
    "qwen": "dashscope_api_key",
}
_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "qwen": "DASHSCOPE_API_KEY",
}


def has_llm_credentials(settings: Settings, provider: Optional[str] = None) -> bool:
    """Local providers need no key; hosted ones do.

    `provider` defaults to the one this request is using.
    """
    provider = provider or settings.active_provider
    if provider in ("ollama", "vllm"):
        return True
    return bool(getattr(settings, _KEY_SETTING.get(provider, ""), ""))


def missing_credentials_reason(settings: Settings, provider: Optional[str] = None) -> str:
    """A message that names the fix rather than just the failure."""
    provider = provider or settings.active_provider
    key = _KEY_ENV.get(provider)
    fix = f"Set {key} in .env" if key else "Set the provider's API key in .env"
    return (
        f"No credentials configured for {provider}. {fix}, "
        "or choose another model provider in Settings."
    )


def available_providers(settings: Settings) -> list[str]:
    """Providers a user may switch to: every hosted one with a key, and the
    local ones only when they are the configured default, since an Ollama or
    vLLM server the deployment never set up would fail on first use."""
    out = []
    for name in PROVIDER_NAMES:
        if name in ("ollama", "vllm"):
            if name == settings.model_provider:
                out.append(name)
        elif has_llm_credentials(settings, name):
            out.append(name)
    return out
