"""User feedback (thumbs up/down) endpoint — feeds the evaluation loop."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

_feedback_store: list[dict] = []


class FeedbackRequest(BaseModel):
    run_id: str
    user_id: str
    score: int                   # -1 (thumbs down) or 1 (thumbs up)
    comment: Optional[str] = None


@router.post("")
async def submit_feedback(req: FeedbackRequest):
    if req.score not in (-1, 1):
        raise HTTPException(status_code=400, detail="score must be -1 or 1")
    entry = {
        "run_id": req.run_id,
        "user_id": req.user_id,
        "score": req.score,
        "comment": req.comment,
        "created_at": datetime.now().isoformat(),
    }
    _feedback_store.append(entry)
    return {"status": "recorded", "run_id": req.run_id}


@router.get("/summary")
async def feedback_summary():
    """Aggregate feedback scores — used by the evaluation dashboard."""
    if not _feedback_store:
        return {"total": 0, "positive": 0, "negative": 0, "score": None}
    pos = sum(1 for f in _feedback_store if f["score"] == 1)
    neg = sum(1 for f in _feedback_store if f["score"] == -1)
    return {
        "total": len(_feedback_store),
        "positive": pos,
        "negative": neg,
        "score": round(pos / len(_feedback_store), 2),
    }
