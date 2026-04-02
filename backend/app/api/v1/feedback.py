"""User feedback (thumbs up/down) endpoint -- feeds the evaluation loop (DB-backed, Phase 2)."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories import create_feedback, ensure_user, get_feedback_summary
from app.db.session import get_db

router = APIRouter()


class FeedbackRequest(BaseModel):
    run_id: str
    user_id: str
    score: int  # -1 (thumbs down) or 1 (thumbs up)
    comment: Optional[str] = None


@router.post("")
async def submit_feedback(req: FeedbackRequest, db: AsyncSession = Depends(get_db)):
    if req.score not in (-1, 1):
        raise HTTPException(status_code=400, detail="score must be -1 or 1")
    await ensure_user(db, req.user_id)
    await create_feedback(db, req.run_id, req.user_id, req.score, req.comment)
    return {"status": "recorded", "run_id": req.run_id}


@router.get("/summary")
async def feedback_summary(db: AsyncSession = Depends(get_db)):
    """Aggregate feedback scores -- used by the evaluation dashboard."""
    return await get_feedback_summary(db)
