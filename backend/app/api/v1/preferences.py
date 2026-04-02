"""User preferences (memory) endpoints."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

# In-memory store for Phase 1 — backed by Postgres in Phase 2
_preferences: dict[str, dict] = {}


class PreferencesUpdate(BaseModel):
    risk_tolerance: Optional[str] = None         # conservative | moderate | aggressive
    investment_horizon: Optional[str] = None     # short | medium | long
    preferred_sectors: Optional[list[str]] = None
    analysis_depth: Optional[str] = None         # quick | standard | deep
    preferred_metrics: Optional[list[str]] = None


@router.get("")
async def get_preferences(user_id: str):
    return _preferences.get(user_id, {
        "user_id": user_id,
        "risk_tolerance": "moderate",
        "investment_horizon": "medium",
        "preferred_sectors": [],
        "analysis_depth": "standard",
        "preferred_metrics": [],
        "recent_tickers": [],
    })


@router.put("")
async def update_preferences(user_id: str, req: PreferencesUpdate):
    prefs = _preferences.get(user_id, {"user_id": user_id})
    if req.risk_tolerance is not None:
        prefs["risk_tolerance"] = req.risk_tolerance
    if req.investment_horizon is not None:
        prefs["investment_horizon"] = req.investment_horizon
    if req.preferred_sectors is not None:
        prefs["preferred_sectors"] = req.preferred_sectors
    if req.analysis_depth is not None:
        prefs["analysis_depth"] = req.analysis_depth
    if req.preferred_metrics is not None:
        prefs["preferred_metrics"] = req.preferred_metrics
    _preferences[user_id] = prefs
    return prefs
