"""Crowd conditions, named and weighed — the social half of the Signals page.

The same contract as `services/signals.py`, applied to what people are saying
instead of what price is doing: deterministic rules over readings that have
already been fetched, each with its evidence and the observable event that
would end it. A language model may narrate these; it never decides them.

Kept apart from `signals.py` on purpose:

* **Different input, independent failure.** StockTwits being blocked must not
  change a single chip on the chart.
* **Different kind of evidence.** Folding Reddit volume into a chart reading's
  net bias would let noise tilt it — and attention extremes are as often a
  late signal as an early one.
* **Its own disclaimer**, since "past price data" is not what these are.

**Attention has no direction.** A mention spike says people are talking, not
which way; it is neutral in every rule below. Only self-tagged crowd messages
carry a direction, and they are graded `low` reliability because the tags are
self-selected and the samples small.

Pure and synchronous, like `signals.py`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from app.services.signals import Signal, SignalSet, _conflicts, _net_bias, _score
from app.services.social.history import MIN_HISTORY_DAYS
from app.services.social.models import CrowdTags, RedditAttention, SocialPositioning

SOCIAL_DISCLAIMER = (
    "These describe online discussion, not the company. Mention counts and "
    "self-tagged posts are noisy, easy to inflate, and often loudest after a "
    "move rather than before it. Not a recommendation."
)

# ── Attention (Reddit via ApeWisdom) ───────────────────────────────────────
SPIKE_Z = 2.0                 # robust z vs the stored daily history
SURGE_MULTIPLE = 3.0          # vs 24h ago, while history is too short for z
MIN_MENTIONS = 25             # below this a "3x" or a 300-place climb is a few posts
RANK_CLIMB_PLACES = 15
TOP_RANK = 20
FADE_MULTIPLE = 0.5

# ── Crowd tags (StockTwits) ────────────────────────────────────────────────
# Asymmetric on purpose. Users tag mostly what they own, so ~60–65% bullish
# is the resting level of the tape, not a lean. 80% is a real tilt up; 45% —
# below even odds on a bullish-skewed scale — is a real tilt down.
MIN_TAGGED = 15
CROWD_BULLISH_PCT = 80.0
CROWD_BEARISH_PCT = 45.0
ONE_SIDED_PCT = 90.0

# ── Positioning ────────────────────────────────────────────────────────────
SHORT_INTEREST_HIGH_PCT = 20.0

DECLARED_IDS = frozenset({
    "mention_spike", "mention_surge_24h", "rank_climb", "attention_fading",
    "crowd_bullish", "crowd_bearish", "crowd_one_sided", "short_interest_high",
})


def _clip(value: float) -> float:
    return round(min(1.0, max(0.0, value)), 4)


def _get(source: Any, name: str) -> Any:
    if source is None:
        return None
    if isinstance(source, dict):
        return source.get(name)
    return getattr(source, name, None)


# ── Rules ──────────────────────────────────────────────────────────────────


def attention_signals(symbol: str, reading: Any) -> list[Signal]:
    """Rules over one mention-board reading (a RedditAttention or TrendingRow)."""
    rank = _get(reading, "rank")
    mentions = _get(reading, "mentions")
    if rank is None or mentions is None:
        return []

    before = _get(reading, "mentions_24h_ago")
    rank_before = _get(reading, "rank_24h_ago")
    rank_change = _get(reading, "rank_change")
    change_pct = _get(reading, "mention_change_pct")
    z = _get(reading, "attention_z")
    days = _get(reading, "history_days") or 0
    out: list[Signal] = []

    base_evidence = {"rank": rank, "mentions": mentions, "mentions_24h_ago": before,
                     "rank_24h_ago": rank_before}

    has_history = days >= MIN_HISTORY_DAYS and z is not None
    if has_history and z >= SPIKE_Z:
        out.append(Signal(
            id="mention_spike", label="Mention spike", category="social",
            direction="neutral", timeframe="short", strength=_clip(z / 5.0),
            reliability="low",
            evidence={**base_evidence, "attention_z": z, "history_days": days},
            rationale=(f"{symbol} has {mentions} Reddit mentions in 24 hours, "
                       f"{z:.1f} robust standard deviations above its {days}-day "
                       f"baseline. Attention is unusual; it says nothing about direction."),
            invalidation="Ends when daily mentions fall back within 1 deviation of the baseline median.",
        ))

    multiple = (mentions / before) if before else None
    if (not has_history and multiple is not None and multiple >= SURGE_MULTIPLE
            and mentions >= MIN_MENTIONS):
        out.append(Signal(
            id="mention_surge_24h", label="Mentions surging", category="social",
            direction="neutral", timeframe="short", strength=_clip(multiple / 6.0),
            reliability="low",
            evidence={**base_evidence, "mention_change_pct": change_pct,
                      "multiple": round(multiple, 1), "history_days": days},
            rationale=(f"{symbol} has {mentions} Reddit mentions against {before} a day "
                       f"earlier, {multiple:.1f} times as many. With under "
                       f"{MIN_HISTORY_DAYS} days of stored history this compares only "
                       f"against yesterday, not a baseline."),
            invalidation="Ends when mentions drop below three times the prior day's count.",
        ))

    # Deep in the board a handful of posts moves a ticker hundreds of places,
    # so a climb only counts once there is real volume behind it.
    newly_top = rank <= TOP_RANK and (rank_before is None or rank_before > TOP_RANK)
    climbed = rank_change is not None and rank_change >= RANK_CLIMB_PLACES
    if (climbed or newly_top) and mentions >= MIN_MENTIONS:
        places = rank_change if rank_change is not None else None
        out.append(Signal(
            id="rank_climb", label="Climbing the board", category="social",
            direction="neutral", timeframe="short",
            strength=_clip((places or RANK_CLIMB_PLACES) / 50.0), reliability="low",
            evidence={**base_evidence, "rank_change": places},
            rationale=(f"{symbol} moved from rank {rank_before} to rank {rank} on the "
                       f"Reddit mention board in 24 hours." if rank_before is not None else
                       f"{symbol} entered the Reddit mention board at rank {rank} "
                       f"within 24 hours."),
            invalidation=f"Ends when it drops back out of the top {TOP_RANK} "
                         f"or loses the places it gained.",
        ))

    if (rank_before is not None and rank_before <= TOP_RANK and before
            and mentions <= before * FADE_MULTIPLE):
        out.append(Signal(
            id="attention_fading", label="Attention fading", category="social",
            direction="neutral", timeframe="short",
            strength=_clip(1 - mentions / before), reliability="low",
            evidence={**base_evidence, "mention_change_pct": change_pct},
            rationale=(f"{symbol} was rank {rank_before} a day ago with {before} mentions "
                       f"and now has {mentions}, under half. The discussion is cooling."),
            invalidation="Ends when mentions recover to at least half the prior day's count.",
        ))

    return out


def crowd_signals(symbol: str, crowd: Optional[CrowdTags], attention_active: bool) -> list[Signal]:
    if crowd is None or not crowd.available or crowd.bull_pct is None:
        return []
    tagged = crowd.bullish + crowd.bearish
    if tagged < MIN_TAGGED:
        return []

    pct = crowd.bull_pct
    evidence = {"bull_pct": pct, "bear_pct": round(100.0 - pct, 1), "tagged": tagged,
                "bullish": crowd.bullish, "bearish": crowd.bearish,
                "sampled": crowd.sampled, "window_hours": crowd.window_hours}
    out: list[Signal] = []

    if pct >= CROWD_BULLISH_PCT:
        out.append(Signal(
            id="crowd_bullish", label="Crowd tags bullish", category="social",
            direction="bullish", timeframe="short",
            strength=_clip((pct - CROWD_BULLISH_PCT) / (100 - CROWD_BULLISH_PCT) + 0.25),
            reliability="low", evidence=evidence,
            rationale=(f"{pct:.0f}% of {tagged} tagged StockTwits messages on {symbol} are "
                       f"self-tagged bullish. Tags lean bullish by habit, so this is a "
                       f"tilt above an already-bullish resting level."),
            invalidation=f"Ends when the bullish share of tagged messages falls below "
                         f"{CROWD_BULLISH_PCT:.0f}%.",
        ))
    elif pct <= CROWD_BEARISH_PCT:
        out.append(Signal(
            id="crowd_bearish", label="Crowd tags bearish", category="social",
            direction="bearish", timeframe="short",
            strength=_clip((CROWD_BEARISH_PCT - pct) / CROWD_BEARISH_PCT + 0.25),
            reliability="low", evidence=evidence,
            rationale=(f"Only {pct:.0f}% of {tagged} tagged StockTwits messages on {symbol} "
                       f"are bullish. On a tape where tags skew bullish by habit, that is "
                       f"an unusually negative crowd."),
            invalidation=f"Ends when the bullish share of tagged messages rises above "
                         f"{CROWD_BEARISH_PCT:.0f}%.",
        ))

    if pct >= ONE_SIDED_PCT and attention_active:
        out.append(Signal(
            id="crowd_one_sided", label="One-sided crowd", category="social",
            direction="neutral", timeframe="short", strength=_clip(pct / 100.0),
            reliability="low", evidence=evidence,
            rationale=(f"Attention on {symbol} is elevated and {pct:.0f}% of tagged messages "
                       f"agree. When nearly everyone talking already leans the same way, "
                       f"few voices are left to join it."),
            invalidation="Ends when attention normalises or the tagged split falls "
                         f"below {ONE_SIDED_PCT:.0f}% bullish.",
        ))
    return out


def positioning_signals(symbol: str, positioning: Optional[SocialPositioning]) -> list[Signal]:
    spf = positioning.short_percent_float if positioning else None
    if spf is None or spf < SHORT_INTEREST_HIGH_PCT:
        return []
    return [Signal(
        id="short_interest_high", label="High short interest", category="social",
        direction="neutral", timeframe="medium",
        strength=_clip((spf - SHORT_INTEREST_HIGH_PCT) / 30.0 + 0.2), reliability="medium",
        evidence={"short_percent_float": spf, "short_ratio": positioning.short_ratio,
                  "short_change_pct": positioning.short_change_pct},
        rationale=(f"{spf:.1f}% of {symbol}'s float is sold short. Heavy short interest "
                   f"means a large group is positioned against the shares, which can "
                   f"amplify moves in either direction."),
        invalidation=f"Ends when reported short interest falls below "
                     f"{SHORT_INTEREST_HIGH_PCT:.0f}% of float (reported twice monthly).",
    )]


# ── Assembly ───────────────────────────────────────────────────────────────


def trending_signal_ids(row: Any) -> list[str]:
    """Compact badge ids for one trending-board row. Attention rules only."""
    symbol = _get(row, "symbol") or ""
    return [s.id for s in attention_signals(symbol, row)]


def derive_social_signals(
    symbol: str,
    *,
    reddit: Optional[RedditAttention] = None,
    crowd: Optional[CrowdTags] = None,
    positioning: Optional[SocialPositioning] = None,
    as_of: Optional[datetime] = None,
) -> SignalSet:
    """Every crowd condition currently true of one ticker. Pure."""
    symbol = symbol.upper()
    attention = attention_signals(symbol, reddit) if reddit and reddit.available else []
    active = any(s.id in ("mention_spike", "mention_surge_24h", "rank_climb") for s in attention)
    signals = [
        *attention,
        *crowd_signals(symbol, crowd, active),
        *positioning_signals(symbol, positioning),
    ]
    score = _score(signals)
    return SignalSet(
        ticker=symbol,
        as_of=as_of or datetime.now(timezone.utc),
        signals=signals,
        net_bias=_net_bias(signals, score),
        bias_score=score,
        conflicts=_conflicts(signals),
        coverage={
            "reddit": bool(reddit and reddit.available),
            "stocktwits": bool(crowd and crowd.available),
            "history": bool(reddit and reddit.history_days >= MIN_HISTORY_DAYS),
            "positioning": bool(positioning and positioning.short_percent_float is not None),
        },
        disclaimer=SOCIAL_DISCLAIMER,
    )
