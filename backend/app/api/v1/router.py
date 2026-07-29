"""Aggregate all v1 API routes."""

from fastapi import APIRouter

from app.api.v1 import analysis, health, market, preferences, watchlist, feedback, upload

api_router = APIRouter()

api_router.include_router(health.router, tags=["health"])
api_router.include_router(analysis.router, prefix="/analysis", tags=["analysis"])
api_router.include_router(watchlist.router, prefix="/watchlist", tags=["watchlist"])
api_router.include_router(preferences.router, prefix="/preferences", tags=["preferences"])
api_router.include_router(feedback.router, prefix="/feedback", tags=["feedback"])
api_router.include_router(upload.router, prefix="/upload", tags=["upload"])
api_router.include_router(market.router, prefix="/market", tags=["market"])
