"""The Signals page's service layer: trending, ticker snapshot, explanation.

Assembles what the providers fetch and what the rules derive. The heavy
lifting is elsewhere — `services/social/` fetches, `services/social_signals.py`
decides, `services/signals.py` reads the chart — so this module is mostly
plumbing plus the one place a language model is allowed near these numbers:
`explain_ticker`, which narrates a brief and is checked before it is shown.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Optional

import structlog

from app.agents.base.guardrails import check_investment_advice, check_numeric_grounding
from app.config import get_settings
from app.db.repositories_market import MARKET_TZ
from app.services import market_data
from app.services import technicals as technicals_service
from app.services.cache import cached_async
from app.services.llm_guard import has_llm_credentials, missing_credentials_reason
from app.services.signals import derive_signals
from app.services.social import apewisdom, get_provider, statuses, stocktwits
from app.services.social.history import attention_stats, daily_series
from app.services.social.models import HistoryPoint, SocialPositioning
from app.services.social_signals import (
    SOCIAL_DISCLAIMER,
    derive_social_signals,
    trending_signal_ids,
)

log = structlog.get_logger(__name__)

HISTORY_DAYS = 30
SNAPSHOT_TTL = 120.0
EXPLANATION_TTL = 900.0
HISTORY_SOURCE = "apewisdom"


# ── history ─────────────────────────────────────────────────────────────────


def _first_line(e: Exception) -> str:
    """SQLAlchemy errors embed the whole statement and every bound parameter."""
    return (str(e).splitlines() or [type(e).__name__])[0][:200]


async def _load_history(symbols: list[str]) -> dict[str, list]:
    """Daily (date, mentions, rank) per symbol. Empty when the store is down."""
    if not symbols:
        return {}
    try:
        from app.db.repositories_social import snapshots_for
        from app.db.session import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            raw = await snapshots_for(session, HISTORY_SOURCE, symbols, HISTORY_DAYS + 1)
    except Exception as e:
        # History is a refinement; without it the rules use the 24h comparison.
        log.info("social_history_unavailable", error=_first_line(e))
        return {}
    return {symbol: daily_series(rows) for symbol, rows in raw.items()}


def _stats_for(daily: list, mentions: Optional[int]):
    today = datetime.now(MARKET_TZ).date()
    prior = [m for day, m, _ in daily if day < today]
    return attention_stats(prior, mentions)


_last_recorded: dict[str, datetime] = {}


async def record_board(rows: list[dict], source: str = HISTORY_SOURCE) -> int:
    """Snapshot board rows into the history store, at most once per hour per process."""
    from app.db.repositories_social import hour_bucket, upsert_snapshots
    from app.db.session import AsyncSessionLocal

    bucket = hour_bucket(datetime.now(timezone.utc))
    if _last_recorded.get(source) == bucket:
        return 0
    try:
        async with AsyncSessionLocal() as session:
            written = await upsert_snapshots(session, source, rows)
            await session.commit()
    except Exception as e:
        log.info("social_snapshot_skipped", error=_first_line(e))
        return 0
    _last_recorded[source] = bucket
    return written


# ── trending ────────────────────────────────────────────────────────────────


async def get_sources() -> dict:
    return {
        "sources": [s.model_dump() for s in await statuses()],
        "filters": [{"id": k, "label": v} for k, v in apewisdom.FILTERS.items()],
        "disclaimer": SOCIAL_DISCLAIMER,
    }


async def get_trending(source: str = "apewisdom", filter: str = apewisdom.DEFAULT_FILTER,
                       limit: int = 50) -> dict:
    provider = get_provider(source)
    if provider is None:
        return {"source": source, "rows": [], "available": False,
                "error": f"unknown source {source!r}"}
    if "trending" not in provider.capabilities:
        return {"source": source, "rows": [], "available": False,
                "error": f"{provider.label} has no trending board"}

    result = await provider.trending(filter=filter, limit=limit)

    # Stored history is the all-stocks board, so the z-score is only
    # meaningful against that filter.
    if source == HISTORY_SOURCE and result.available and filter == apewisdom.DEFAULT_FILTER:
        history = await _load_history([r.symbol for r in result.rows])
        for row in result.rows:
            stats = _stats_for(history.get(row.symbol, []), row.mentions)
            row.attention_z, row.history_days = stats.z, stats.history_days
        if not result.stale and result.rows:
            await record_board([r.model_dump() for r in result.rows])

    for row in result.rows:
        row.signal_ids = trending_signal_ids(row)

    return result.model_dump(mode="json")


# ── ticker snapshot ─────────────────────────────────────────────────────────


def positioning_from(profile: dict) -> SocialPositioning:
    shares, prior = profile.get("shares_short"), profile.get("shares_short_prior_month")
    return SocialPositioning(
        short_percent_float=profile.get("short_percent_float"),
        short_ratio=profile.get("short_ratio"),
        shares_short=shares,
        shares_short_prior_month=prior,
        short_change_pct=round((shares - prior) / prior * 100, 1) if shares and prior else None,
    )


async def _settle(coro, label: str, symbol: str) -> Any:
    try:
        return await coro
    except Exception as e:
        log.warning("signals_snapshot_part_failed", part=label, symbol=symbol, error=str(e))
        return None


async def build_ticker_snapshot(symbol: str) -> dict:
    symbol = symbol.upper()
    reddit, crowd, profile, technicals, history = await asyncio.gather(
        _settle(apewisdom.ticker(symbol), "reddit", symbol),
        _settle(stocktwits.ticker(symbol), "stocktwits", symbol),
        _settle(market_data.get_profile(symbol), "profile", symbol),
        _settle(technicals_service.get_technicals(symbol), "technicals", symbol),
        _settle(_load_history([symbol]), "history", symbol),
    )
    profile = profile if isinstance(profile, dict) and "error" not in profile else {}

    if reddit is not None:
        daily = (history or {}).get(symbol, [])
        stats = _stats_for(daily, reddit.mentions)
        reddit.attention_z, reddit.history_days = stats.z, stats.history_days
        reddit.history = [HistoryPoint(date=d.isoformat(), mentions=m, rank=r)
                          for d, m, r in daily[-HISTORY_DAYS:]]

    positioning = positioning_from(profile)
    social_set = derive_social_signals(symbol, reddit=reddit, crowd=crowd,
                                       positioning=positioning)
    technical_set = derive_signals(technicals, ticker=symbol) if technicals else None

    return {
        "symbol": symbol,
        "name": profile.get("name") or symbol,
        "sector": profile.get("sector"),
        "as_of": datetime.now(timezone.utc).isoformat(),
        "reddit": reddit.model_dump(mode="json") if reddit else None,
        "stocktwits": crowd.model_dump(mode="json") if crowd else None,
        "positioning": positioning.model_dump(),
        "social_signal_set": social_set.model_dump(mode="json"),
        "technical_signal_set": technical_set.model_dump(mode="json") if technical_set else None,
        "disclaimer": SOCIAL_DISCLAIMER,
    }


async def get_ticker_snapshot(symbol: str) -> dict:
    symbol = symbol.upper().strip()
    if not symbol or len(symbol) > 16:
        return {"symbol": symbol, "error": "invalid symbol"}
    try:
        return await cached_async(f"social:snap:{symbol}", SNAPSHOT_TTL,
                                  lambda: build_ticker_snapshot(symbol))
    except Exception as e:
        log.error("signals_snapshot_failed", symbol=symbol, error=str(e))
        return {"symbol": symbol, "error": str(e)}


# ── explanation ─────────────────────────────────────────────────────────────

EXPLAIN_SYSTEM_PROMPT = """You describe what online discussion about one stock looks like right now.

You will be given a brief: the ticker's place on the Reddit mention board, its
self-tagged StockTwits split if available, short interest, and the crowd
conditions computed from them, each with a rationale. Every number is already
in the units to quote it in.

Write 2 short paragraphs:
1. How much attention the ticker is getting and how that has changed —
   rank, mentions, and the change against the day before or the baseline.
2. What the crowd's own tags and the positioning say, with the caveats that
   apply (tags skew bullish by habit; small samples; mentions are not
   direction).

Rules:
- Describe the discussion. Do not say what the stock will do or is worth.
- Do not recommend buying, selling or holding, in any wording.
- Quote only numbers that appear in the brief, as they appear there.
- If a source is unavailable, say so in a short clause rather than guessing.
- Plain prose, no headings or bullets. Under 150 words total."""


def brief(snapshot: dict) -> dict:
    """The snapshot in prose scale — the model's input and its grounding evidence."""
    reddit = snapshot.get("reddit") or {}
    crowd = snapshot.get("stocktwits") or {}
    signal_set = snapshot.get("social_signal_set") or {}
    return {
        "ticker": snapshot.get("symbol"),
        "name": snapshot.get("name"),
        "reddit": {k: reddit.get(k) for k in (
            "available", "caveat", "rank", "rank_24h_ago", "rank_change", "mentions",
            "mentions_24h_ago", "mention_change_pct", "upvotes", "attention_z", "history_days",
        )} | {"window_hours": 24},   # ApeWisdom counts are rolling 24-hour windows
        "stocktwits": {k: crowd.get(k) for k in (
            "available", "caveat", "sampled", "bullish", "bearish", "untagged",
            "bull_pct", "window_hours",
        )} | {"tagged": (crowd.get("bullish") or 0) + (crowd.get("bearish") or 0)},
        "positioning": snapshot.get("positioning") or {},
        "conditions": [
            {"id": s.get("id"), "label": s.get("label"), "direction": s.get("direction"),
             "rationale": s.get("rationale"), "evidence": s.get("evidence")}
            for s in signal_set.get("signals") or []
        ],
        "net_lean": signal_set.get("bias_phrase"),
    }


def templated_explanation(b: dict) -> str:
    """The fallback, built only from the brief so it grounds by construction."""
    ticker = b.get("ticker") or "This ticker"
    reddit, crowd = b.get("reddit") or {}, b.get("stocktwits") or {}
    paragraphs: list[str] = []

    if reddit.get("rank") is not None:
        line = f"{ticker} is rank {reddit['rank']} on the Reddit mention board with {reddit.get('mentions')} mentions in 24 hours"
        if reddit.get("mentions_24h_ago"):
            line += f", against {reddit['mentions_24h_ago']} the day before"
        paragraphs.append(line + ".")
    elif reddit.get("caveat"):
        paragraphs.append(reddit["caveat"])

    if crowd.get("available") and crowd.get("bull_pct") is not None:
        paragraphs.append(
            f"Of {crowd.get('tagged')} recent StockTwits messages "
            f"carrying a tag, {crowd['bull_pct']}% are tagged bullish. Tags lean bullish by "
            "habit, so only a share well above that resting level says much."
        )
    elif crowd.get("caveat"):
        paragraphs.append(crowd["caveat"])

    rationales = [c["rationale"] for c in b.get("conditions") or [] if c.get("rationale")]
    if rationales:
        paragraphs.append(" ".join(rationales[:3]))
    return "\n\n".join(paragraphs) or f"There is too little discussion data on {ticker} to describe."


def review(text: str, b: dict) -> list[str]:
    problems = [f"advice: {hit}" for hit in check_investment_advice(text, mode="chat")]
    problems += [f"ungrounded: {n}" for n in check_numeric_grounding(text, b)]
    return problems


async def explain_ticker(symbol: str) -> dict:
    from app.models.factory import get_provider as get_llm

    symbol = symbol.upper()
    settings = get_settings()
    if not has_llm_credentials(settings):
        return {"symbol": symbol, "available": False,
                "reason": missing_credentials_reason(settings)}

    snapshot = await get_ticker_snapshot(symbol)
    if "error" in snapshot:
        return {"symbol": symbol, "available": False, "reason": snapshot["error"]}
    b = brief(snapshot)

    async def fetch() -> dict:
        response = await get_llm().complete(
            messages=[
                {"role": "system", "content": EXPLAIN_SYSTEM_PROMPT},
                {"role": "user", "content": f"Discussion brief for {symbol}:\n\n"
                                            f"{json.dumps(b, default=str)}"},
            ],
            model=settings.fast_model, temperature=0.2, max_tokens=450,
        )
        text = response.content.strip()
        problems = review(text, b)
        if problems:
            log.warning("social_explain_guarded", symbol=symbol, problems=problems)
            text = templated_explanation(b)
        return {
            "symbol": symbol, "available": True, "explanation": text,
            "guarded": bool(problems), "model": response.model,
            "disclaimer": SOCIAL_DISCLAIMER,
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    try:
        return await cached_async(
            f"social:explain:{settings.active_provider}:{symbol}", EXPLANATION_TTL, fetch,
            should_cache=lambda r: bool(r.get("explanation")),
        )
    except Exception as e:
        log.error("social_explain_failed", symbol=symbol, error=str(e))
        return {"symbol": symbol, "available": False, "reason": str(e)}
