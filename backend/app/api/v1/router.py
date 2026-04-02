"""Aggregate all v1 API routes."""

from fastapi import APIRouter

from app.api.v1 import analysis, health, preferences, watchlist, feedback

api_router = APIRouter()

api_router.include_router(health.router, tags=["health"])
api_router.include_router(analysis.router, prefix="/analysis", tags=["analysis"])
api_router.include_router(watchlist.router, prefix="/watchlist", tags=["watchlist"])
api_router.include_router(preferences.router, prefix="/preferences", tags=["preferences"])
api_router.include_router(feedback.router, prefix="/feedback", tags=["feedback"])
