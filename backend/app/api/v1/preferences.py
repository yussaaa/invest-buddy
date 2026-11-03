"""User preferences (memory) endpoints (DB-backed, Phase 2)."""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import PROVIDER_NAMES, get_settings
from app.models.factory import _client_for

from app.db.repositories import (
    ensure_user,
    get_or_create_preferences,
    update_preferences,
)
from app.db.session import get_db
from app.services.llm_guard import available_providers, missing_credentials_reason

router = APIRouter()


class PreferencesUpdate(BaseModel):
    risk_tolerance: Optional[str] = None  # conservative | moderate | aggressive
    investment_horizon: Optional[str] = None  # short | medium | long
    preferred_sectors: Optional[list[str]] = None
    analysis_depth: Optional[str] = None  # quick | standard | deep
    preferred_metrics: Optional[list[str]] = None
    # A provider name, or "" to go back to the server default.
    llm_provider: Optional[str] = None


PROVIDER_LABELS = {
    "openai": "OpenAI",
    "anthropic": "Anthropic (Claude)",
    "qwen": "Qwen (DashScope)",
    "ollama": "Ollama (local)",
    "vllm": "vLLM (local)",
}


@router.get("/llm-providers")
async def list_llm_providers():
    """Every provider, whether it can be chosen, and the models it would use.

    Availability means credentials in .env today; per-user keys would extend
    the same check rather than replace it.
    """
    settings = get_settings()
    usable = set(available_providers(settings))
    providers = []
    for name in PROVIDER_NAMES:
        fast, smart = settings.models_for(name)
        entry = {
            "id": name,
            "label": PROVIDER_LABELS[name],
            "available": name in usable,
            "fast_model": fast,
            "smart_model": smart,
        }
        if name not in usable:
            entry["reason"] = (
                "Local server not configured on this deployment"
                if name in ("ollama", "vllm")
                else missing_credentials_reason(settings, name)
            )
        providers.append(entry)
    return {"default": settings.model_provider, "providers": providers}


@router.get("")
async def get_preferences(user_id: str, db: AsyncSession = Depends(get_db)):
    await ensure_user(db, user_id)
    prefs = await get_or_create_preferences(db, user_id)
    return prefs.to_dict()


CONNECTION_TEST_TTL = 60.0       # seconds a result is reused across page loads
CONNECTION_TEST_TIMEOUT = 20.0
_connection_tests: dict[str, tuple[float, dict]] = {}


def _short_error(exc: Exception) -> str:
    """The provider's own message, without the request dump around it."""
    status = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)
    message = None
    if isinstance(body, dict):
        err = body.get("error", body)
        message = err.get("message") if isinstance(err, dict) else None
    message = message or getattr(exc, "message", None) or str(exc) or type(exc).__name__
    return f"{status}: {message}" if status else message


@router.post("/llm-providers/{provider}/test")
async def test_llm_provider(provider: str, fresh: bool = False):
    """One tiny completion on the provider's fast tier.

    A key in .env says nothing about whether it works — an exhausted OpenAI
    balance or a revoked key only shows up on a real call. Results are reused
    for a minute so reopening Settings doesn't re-bill every provider.
    """
    settings = get_settings()
    if provider not in PROVIDER_NAMES:
        raise HTTPException(404, f"Unknown model provider '{provider}'")
    if provider not in available_providers(settings):
        return {"id": provider, "ok": False, "error": "Not configured", "latency_ms": None}

    cached = _connection_tests.get(provider)
    if cached and not fresh and time.monotonic() - cached[0] < CONNECTION_TEST_TTL:
        return cached[1]

    model = settings.models_for(provider)[0]
    started = time.monotonic()
    try:
        response = await asyncio.wait_for(
            _client_for(provider).complete(
                [{"role": "user", "content": "Reply with the single word: ok"}],
                model=model,
                max_tokens=16,
            ),
            CONNECTION_TEST_TIMEOUT,
        )
        result = {"id": provider, "ok": True, "model": response.model, "error": None}
    except asyncio.TimeoutError:
        result = {"id": provider, "ok": False, "model": model,
                  "error": f"No reply within {CONNECTION_TEST_TIMEOUT:.0f}s"}
    except Exception as exc:
        result = {"id": provider, "ok": False, "model": model, "error": _short_error(exc)}
    result["latency_ms"] = int((time.monotonic() - started) * 1000)
    _connection_tests[provider] = (time.monotonic(), result)
    return result


@router.put("")
async def update_preferences_endpoint(
    user_id: str, req: PreferencesUpdate, db: AsyncSession = Depends(get_db)
):
    if req.llm_provider and req.llm_provider not in available_providers(get_settings()):
        raise HTTPException(422, f"Model provider '{req.llm_provider}' is not available")
    await ensure_user(db, user_id)
    prefs = await update_preferences(
        db,
        user_id,
        risk_tolerance=req.risk_tolerance,
        investment_horizon=req.investment_horizon,
        preferred_sectors=req.preferred_sectors,
        analysis_depth=req.analysis_depth,
        preferred_metrics=req.preferred_metrics,
        llm_provider=req.llm_provider,
    )
    return prefs.to_dict()
