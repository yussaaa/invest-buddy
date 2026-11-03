"""Shapes shared by every social provider.

Readings, not opinions: counts, ranks and self-reported tags. Anything that
interprets them lives in `services/social_signals.py`, where it is pure and
tested. Every reading carries `available` and `caveat`, because a source that
is down, blocked or unconfigured is common here, and saying so beats an empty
table that looks like "nobody is talking about this".
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

ProviderKind = Literal["live", "placeholder"]
ProviderState = Literal["live", "blocked", "disabled", "not_configured"]


class ProviderStatus(BaseModel):
    id: str
    label: str
    kind: ProviderKind
    capabilities: list[str]
    enabled: bool
    state: ProviderState
    reason: Optional[str] = None


class Reading(BaseModel):
    """Base for one source's answer about one thing."""

    source: str
    available: bool = True
    caveat: Optional[str] = None
    stale: bool = False


class TrendingRow(BaseModel):
    symbol: str
    name: str
    rank: int
    rank_24h_ago: Optional[int] = None
    # Positive means it climbed: rank 14 → 6 is +8.
    rank_change: Optional[int] = None
    mentions: int
    mentions_24h_ago: Optional[int] = None
    mention_change_pct: Optional[float] = None
    upvotes: Optional[int] = None
    # Filled from stored history once there is enough of it (history.py).
    attention_z: Optional[float] = None
    history_days: int = 0
    signal_ids: list[str] = Field(default_factory=list)


class TrendingResult(Reading):
    filter: str
    as_of: datetime
    total: Optional[int] = None
    rows: list[TrendingRow] = Field(default_factory=list)


class HistoryPoint(BaseModel):
    date: str
    mentions: int
    rank: Optional[int] = None


class RedditAttention(Reading):
    """Where one ticker sits on the Reddit mention board."""

    symbol: str
    rank: Optional[int] = None
    rank_24h_ago: Optional[int] = None
    rank_change: Optional[int] = None
    mentions: Optional[int] = None
    mentions_24h_ago: Optional[int] = None
    mention_change_pct: Optional[float] = None
    upvotes: Optional[int] = None
    attention_z: Optional[float] = None
    history_days: int = 0
    history: list[HistoryPoint] = Field(default_factory=list)
    url: Optional[str] = None


class CrowdTags(Reading):
    """Self-reported Bullish/Bearish tags on recent messages.

    Message bodies are deliberately absent: StockTwits' terms restrict storing
    them, and this payload is cached in Redis. Counts are all the signal uses.
    """

    symbol: str
    sampled: int = 0
    bullish: int = 0
    bearish: int = 0
    untagged: int = 0
    # Share of *tagged* messages that are bullish, 0..100. A percent rather
    # than a ratio so a narration quoting "72%" grounds against it directly.
    bull_pct: Optional[float] = None
    window_hours: Optional[float] = None
    watchlist_count: Optional[int] = None
    url: Optional[str] = None


class SocialPositioning(BaseModel):
    """Short interest — the other side of a crowded trade."""

    short_percent_float: Optional[float] = None    # percent, 0..100
    short_ratio: Optional[float] = None            # days to cover
    shares_short: Optional[int] = None
    shares_short_prior_month: Optional[int] = None
    short_change_pct: Optional[float] = None
