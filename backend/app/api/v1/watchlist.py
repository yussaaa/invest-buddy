"""Watchlist CRUD endpoints (DB-backed, Phase 2)."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories import (
    create_watchlist,
    delete_watchlist,
    ensure_user,
    get_watchlists,
    update_watchlist,
)
from app.db.session import get_db

router = APIRouter()


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
async def list_watchlists(user_id: str, db: AsyncSession = Depends(get_db)):
    wls = await get_watchlists(db, user_id)
    return [w.to_dict() for w in wls]


@router.post("")
async def create_watchlist_endpoint(
    req: WatchlistCreate, db: AsyncSession = Depends(get_db)
):
    await ensure_user(db, req.user_id)
    wl = await create_watchlist(db, req.user_id, req.name, req.tickers, req.notes)
    return wl.to_dict()


@router.put("/{wl_id}")
async def update_watchlist_endpoint(
    wl_id: str, req: WatchlistUpdate, db: AsyncSession = Depends(get_db)
):
    wl = await update_watchlist(db, wl_id, req.name, req.tickers, req.notes)
    if not wl:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    return wl.to_dict()


@router.delete("/{wl_id}")
async def delete_watchlist_endpoint(wl_id: str, db: AsyncSession = Depends(get_db)):
    deleted = await delete_watchlist(db, wl_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    return {"deleted": wl_id}
