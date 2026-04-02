"""Watchlist CRUD endpoints (in-memory store for Phase 1)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

# In-memory store (Phase 1)
_watchlists: dict[str, dict] = {}


class WatchlistCreate(BaseModel):
    user_id: str
    name: str
    tickers: list[str] = []
    notes: dict = {}


class WatchlistUpdate(BaseModel):
    name: Optional[str] = None
    tickers: Optional[list[str]] = None
    notes: Optional[dict] = None


@router.get("")
async def get_watchlists(user_id: str):
    return [w for w in _watchlists.values() if w["user_id"] == user_id]


@router.post("")
async def create_watchlist(req: WatchlistCreate):
    wl_id = str(uuid.uuid4())
    wl = {
        "id": wl_id,
        "user_id": req.user_id,
        "name": req.name,
        "tickers": [t.upper() for t in req.tickers],
        "notes": req.notes,
        "created_at": datetime.now().isoformat(),
    }
    _watchlists[wl_id] = wl
    return wl


@router.put("/{wl_id}")
async def update_watchlist(wl_id: str, req: WatchlistUpdate):
    wl = _watchlists.get(wl_id)
    if not wl:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    if req.name is not None:
        wl["name"] = req.name
    if req.tickers is not None:
        wl["tickers"] = [t.upper() for t in req.tickers]
    if req.notes is not None:
        wl["notes"] = req.notes
    return wl


@router.delete("/{wl_id}")
async def delete_watchlist(wl_id: str):
    if wl_id not in _watchlists:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    del _watchlists[wl_id]
    return {"deleted": wl_id}
