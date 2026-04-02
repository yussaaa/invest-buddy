"""Web search tool via Tavily — purpose-built for LLM agents.

Tavily returns structured, LLM-friendly results (not raw HTML).
Falls back gracefully when no API key is configured.
"""

from __future__ import annotations

import asyncio

import structlog

log = structlog.get_logger(__name__)


async def search_web(query: str, max_results: int = 5) -> dict:
    """Search the web for recent financial developments."""
    def _fetch():
        from app.config import get_settings
        settings = get_settings()

        if not settings.tavily_api_key:
            return {
                "query": query,
                "results": [],
                "caveat": "Tavily API key not configured — web search unavailable.",
            }

        try:
            from tavily import TavilyClient
            client = TavilyClient(api_key=settings.tavily_api_key)
            response = client.search(
                query=query,
                search_depth="advanced",
                max_results=max_results,
                include_answer=True,
            )

            results = []
            for r in response.get("results", []):
                results.append({
                    "title": r.get("title"),
                    "url": r.get("url"),
                    "content": (r.get("content") or "")[:500],
                    "score": r.get("score"),
                    "published_date": r.get("published_date"),
                })

            return {
                "query": query,
                "answer": response.get("answer"),  # Tavily's AI-generated answer
                "results": results,
                "total_results": len(results),
            }
        except Exception as e:
            log.error("tavily_error", query=query, error=str(e))
            return {"query": query, "results": [], "error": str(e)}

    return await asyncio.to_thread(_fetch)
