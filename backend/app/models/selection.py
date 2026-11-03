"""Per-request provider choice: the X-LLM-Provider header → a ContextVar.

The Settings page saves the user's provider and the frontend sends it on every
API call. This middleware validates it and sets `config.requested_provider`
for the life of the request, so `settings.fast_model`, `settings.smart_model`
and `get_provider()` all follow it with no change at their call sites. Work
started from a request — the analysis run's background task, the LangGraph
fan-out, a streaming chat reply — inherits it, because asyncio tasks copy the
context they were created in.

A header naming an unknown provider, or one without credentials, is ignored
rather than rejected: the request still runs, on the configured default.

Today the credentials always come from .env. When users bring their own keys,
this is where the lookup goes: resolve the user, then their key, then the
client — the call sites stay as they are.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import get_settings, requested_provider
from app.services.llm_guard import available_providers

HEADER = b"x-llm-provider"


def resolve(value: str | None) -> str | None:
    """The provider to use for a header value, or None for the default."""
    if not value:
        return None
    name = value.strip().lower()
    return name if name in available_providers(get_settings()) else None


class ProviderSelectionMiddleware:
    """Pure ASGI rather than BaseHTTPMiddleware, so a streamed response body
    runs inside the same context as the handler that produced it."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        raw = dict(scope.get("headers") or []).get(HEADER)
        chosen = resolve(raw.decode("latin-1") if raw else None)
        if chosen is None:
            await self.app(scope, receive, send)
            return
        token = requested_provider.set(chosen)
        try:
            await self.app(scope, receive, send)
        finally:
            requested_provider.reset(token)
