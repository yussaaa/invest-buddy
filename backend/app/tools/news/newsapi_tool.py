"""NewsAPI tool — fetch recent news articles for a ticker.

Falls back gracefully when no API key is configured (returns empty list
with a caveat message so agents don't hallucinate news).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import structlog

from app.tools.retry_decorator import retry_network_errors

log = structlog.get_logger(__name__)


async def get_recent_news(
    ticker: str,
    days: int = 7,
    max_results: int = 10,
) -> dict:
    """Fetch recent news articles mentioning the ticker."""

    @retry_network_errors
    def _fetch():
        from app.config import get_settings
        settings = get_settings()

        if not settings.newsapi_key:
            return {
                "ticker": ticker,
                "articles": [],
                "caveat": "NewsAPI key not configured — news data unavailable.",
                "total_results": 0,
            }

        from newsapi import NewsApiClient
        import yfinance as yf

        client = NewsApiClient(api_key=settings.newsapi_key)

        # Get company name from yfinance for better search
        info = yf.Ticker(ticker).info
        company_name = info.get("shortName") or ticker

        from_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        response = client.get_everything(
            q=f'"{ticker}" OR "{company_name}"',
            from_param=from_date,
            language="en",
            sort_by="relevancy",
            page_size=max_results,
        )

        articles = []
        for a in response.get("articles", []):
            articles.append({
                "title": a.get("title"),
                "source": a.get("source", {}).get("name"),
                "url": a.get("url"),
                "published_at": a.get("publishedAt"),
                "description": (a.get("description") or "")[:300],
            })

        return {
            "ticker": ticker.upper(),
            "company_name": company_name,
            "days_back": days,
            "articles": articles,
            "total_results": response.get("totalResults", 0),
        }

    try:
        return await asyncio.to_thread(_fetch)
    except Exception as e:
        log.error("newsapi_error", ticker=ticker, error=str(e))
        return {
            "ticker": ticker,
            "articles": [],
            "error": str(e),
            "total_results": 0,
        }
