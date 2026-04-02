"""User preferences (memory) endpoints (DB-backed, Phase 2)."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories import (
    ensure_user,
    get_or_create_preferences,
    update_preferences,
)
from app.db.session import get_db

router = APIRouter()


class PreferencesUpdate(BaseModel):
    risk_tolerance: Optional[str] = None  # conservative | moderate | aggressive
    investment_horizon: Optional[str] = None  # short | medium | long
    preferred_sectors: Optional[list[str]] = None
    analysis_depth: Optional[str] = None  # quick | standard | deep
    preferred_metrics: Optional[list[str]] = None


@router.get("")
async def get_preferences(user_id: str, db: AsyncSession = Depends(get_db)):
    await ensure_user(db, user_id)
    prefs = await get_or_create_preferences(db, user_id)
    return prefs.to_dict()


@router.put("")
async def update_preferences_endpoint(
    user_id: str, req: PreferencesUpdate, db: AsyncSession = Depends(get_db)
):
    await ensure_user(db, user_id)
    prefs = await update_preferences(
        db,
        user_id,
        risk_tolerance=req.risk_tolerance,
        investment_horizon=req.investment_horizon,
        preferred_sectors=req.preferred_sectors,
        analysis_depth=req.analysis_depth,
        preferred_metrics=req.preferred_metrics,
    )
    return prefs.to_dict()
