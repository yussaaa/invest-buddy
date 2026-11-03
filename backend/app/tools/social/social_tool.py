"""Agent-facing wrappers over the Signals page services.

Thin on purpose: the services already cache in services/cache.py, so these
only condense. An agent's context window is the scarce resource, so payloads
drop what a narration cannot use — history arrays, provider URLs, the
technical signal set (agents that need it compute it themselves).
"""

from __future__ import annotations

from app.services import screens, signals_page


def _signals(signal_set: dict | None) -> list[dict]:
    return [
        {"id": s["id"], "label": s["label"], "direction": s["direction"],
         "rationale": s["rationale"], "evidence": s["evidence"]}
        for s in (signal_set or {}).get("signals", [])
    ]


async def get_social_sentiment(ticker: str) -> dict:
    """Reddit attention, StockTwits self-tags, short interest, and crowd conditions."""
    snapshot = await signals_page.get_ticker_snapshot(ticker)
    if "error" in snapshot:
        return {"ticker": ticker.upper(), "error": snapshot["error"]}

    reddit = snapshot.get("reddit") or {}
    crowd = snapshot.get("stocktwits") or {}
    social = snapshot.get("social_signal_set") or {}
    caveats = [c for c in (reddit.get("caveat"), crowd.get("caveat")) if c]
    return {
        "ticker": snapshot["symbol"],
        "reddit": {k: reddit.get(k) for k in (
            "available", "rank", "rank_24h_ago", "rank_change", "mentions",
            "mentions_24h_ago", "mention_change_pct", "upvotes", "attention_z",
            "history_days")} | {"window_hours": 24, "source": "ApeWisdom"},
        "stocktwits": {k: crowd.get(k) for k in (
            "available", "sampled", "bullish", "bearish", "untagged", "bull_pct",
            "window_hours")} | {"source": "StockTwits"},
        "positioning": snapshot.get("positioning"),
        "conditions": _signals(social),
        "net_lean": social.get("bias_phrase"),
        "coverage": social.get("coverage"),
        "caveats": caveats,
        "disclaimer": snapshot.get("disclaimer"),
    }


async def get_trending_tickers(filter: str = "all-stocks", limit: int = 20) -> dict:
    """The most-discussed tickers on Reddit right now."""
    limit = max(1, min(limit, 50))
    result = await signals_page.get_trending("apewisdom", filter, limit)
    return {
        "filter": result.get("filter", filter),
        "available": result.get("available", False),
        "caveat": result.get("caveat") or result.get("error"),
        "window_hours": 24,
        "rows": [
            {k: row.get(k) for k in ("symbol", "name", "rank", "rank_change", "mentions",
                                     "mentions_24h_ago", "mention_change_pct",
                                     "attention_z", "signal_ids")}
            for row in result.get("rows", [])
        ],
    }


async def get_stock_screen(preset: str = screens.DEFAULT_PRESET, limit: int = 15) -> dict:
    """One of Yahoo's predefined stock screens, with relative volume."""
    result = await screens.get_screen(preset, max(1, min(limit, 25)))
    return {
        "preset": result.get("preset"),
        "label": result.get("label"),
        "error": result.get("error"),
        "rows": [
            {k: row.get(k) for k in ("symbol", "name", "sector", "last", "change_percent",
                                     "rel_volume", "market_cap", "social")}
            for row in result.get("rows", [])
        ],
    }
