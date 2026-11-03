"""The Settings-page provider choice: header → request context → model calls.

Credentials come from .env; these tests set them on the settings singleton so
which providers are "available" is under the test's control.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1 import preferences
from app.config import get_settings, requested_provider
from app.models import factory
from app.models.selection import ProviderSelectionMiddleware
from app.services.llm_guard import available_providers, has_llm_credentials


@pytest.fixture
def settings(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "model_provider", "openai")
    monkeypatch.setattr(s, "openai_api_key", "sk-openai")
    monkeypatch.setattr(s, "anthropic_api_key", "sk-ant")
    monkeypatch.setattr(s, "dashscope_api_key", "")
    monkeypatch.setattr(s, "anthropic_fast_model", "claude-haiku-4-5")
    monkeypatch.setattr(s, "anthropic_smart_model", "claude-opus-5-5")
    return s


@pytest.fixture
def app(settings):
    application = FastAPI()
    application.add_middleware(ProviderSelectionMiddleware)

    @application.get("/which")
    async def which():
        s = get_settings()
        # A task spawned by the handler — the analysis run, the fan-out.
        inherited = await asyncio.create_task(_active())
        return {"provider": s.active_provider, "fast": s.fast_model,
                "smart": s.smart_model, "task": inherited}

    application.include_router(preferences.router, prefix="/preferences")
    return application


async def _active() -> str:
    return get_settings().active_provider


async def _get(app, path, header=None):
    headers = {"X-LLM-Provider": header} if header else {}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return (await c.get(path, headers=headers)).json()


# ── availability ────────────────────────────────────────────────────────────


def test_hosted_providers_need_a_key_and_local_ones_must_be_the_default(settings):
    assert available_providers(settings) == ["openai", "anthropic"]
    assert not has_llm_credentials(settings, "qwen")


def test_a_local_default_is_offered(settings, monkeypatch):
    monkeypatch.setattr(settings, "model_provider", "ollama")
    assert "ollama" in available_providers(settings)
    assert "vllm" not in available_providers(settings)


# ── request scoping ─────────────────────────────────────────────────────────


async def test_header_switches_provider_and_models_for_the_request(app):
    out = await _get(app, "/which", "anthropic")
    assert out == {"provider": "anthropic", "fast": "claude-haiku-4-5",
                   "smart": "claude-opus-5-5", "task": "anthropic"}


async def test_no_header_means_the_configured_default(app, settings):
    out = await _get(app, "/which")
    assert out["provider"] == "openai"
    assert out["fast"] == settings.openai_fast_model


@pytest.mark.parametrize("header", ["qwen", "made-up", "ANTHROPIC "])
async def test_unavailable_or_unknown_choices_fall_back(app, header):
    out = await _get(app, "/which", header)
    expected = "anthropic" if header.strip().lower() == "anthropic" else "openai"
    assert out["provider"] == expected


async def test_choice_does_not_leak_past_the_request(app):
    await _get(app, "/which", "anthropic")
    assert requested_provider.get() is None


def test_get_provider_follows_the_choice_with_one_client_each(settings):
    default = factory.get_provider()
    token = requested_provider.set("anthropic")
    try:
        chosen = factory.get_provider()
        assert type(chosen).__name__ == "AnthropicProvider"
        assert factory.get_provider() is chosen
    finally:
        requested_provider.reset(token)
    assert type(default).__name__ == "OpenAIProvider"


# ── the Settings page's list ────────────────────────────────────────────────


async def test_provider_list_reports_availability_and_models(app):
    out = await _get(app, "/preferences/llm-providers")
    assert out["default"] == "openai"
    by_id = {p["id"]: p for p in out["providers"]}
    assert set(by_id) == {"openai", "anthropic", "qwen", "ollama", "vllm"}
    assert by_id["anthropic"]["available"] is True
    assert by_id["anthropic"]["smart_model"] == "claude-opus-5-5"
    assert by_id["qwen"]["available"] is False
    assert "DASHSCOPE_API_KEY" in by_id["qwen"]["reason"]


# ── connection test ─────────────────────────────────────────────────────────


class _Reply:
    model = "claude-haiku-4-5-20251001"


class _FakeClient:
    def __init__(self, error: Exception | None = None):
        self.error, self.calls = error, 0

    async def complete(self, messages, model, max_tokens):
        self.calls += 1
        if self.error:
            raise self.error
        return _Reply()


class _QuotaError(Exception):
    status_code = 429
    body = {"error": {"message": "You have no credits remaining."}}


async def _post(app, path):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return (await c.post(path)).json()


@pytest.fixture
def fake_clients(monkeypatch):
    preferences._connection_tests.clear()
    clients = {"anthropic": _FakeClient(), "openai": _FakeClient(_QuotaError())}
    monkeypatch.setattr(preferences, "_client_for", lambda name: clients[name])
    yield clients
    preferences._connection_tests.clear()


async def test_a_working_key_reports_connected(app, fake_clients):
    out = await _post(app, "/preferences/llm-providers/anthropic/test")
    assert out["ok"] is True and out["error"] is None
    assert out["latency_ms"] >= 0


async def test_a_key_that_is_present_but_failing_reports_the_providers_reason(app, fake_clients):
    out = await _post(app, "/preferences/llm-providers/openai/test")
    assert out["ok"] is False
    assert out["error"] == "429: You have no credits remaining."


async def test_results_are_reused_unless_fresh_is_asked_for(app, fake_clients):
    path = "/preferences/llm-providers/anthropic/test"
    await _post(app, path)
    await _post(app, path)
    assert fake_clients["anthropic"].calls == 1
    await _post(app, path + "?fresh=true")
    assert fake_clients["anthropic"].calls == 2


async def test_unconfigured_providers_are_not_called(app, fake_clients):
    out = await _post(app, "/preferences/llm-providers/qwen/test")
    assert out == {"id": "qwen", "ok": False, "error": "Not configured", "latency_ms": None}
