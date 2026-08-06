"""Technical conditions, named and weighed — the deterministic half of the chat agent.

The chat agent answers questions like "is this a good time to buy?". The honest
form of that answer is a *conditional setup*: here is the condition that holds,
here is what would invalidate it, here is what disagrees with it. This module
produces that structure; the language model only narrates what it is handed.

Keeping the reasoning here rather than in a prompt buys three things:

  * **It is testable.** Every threshold below is asserted in
    tests/unit/test_signals.py against fixture readings, with no network and no
    model call. A prompt cannot be regression-tested this way.
  * **It cannot hallucinate a number.** The model receives `evidence` and the
    verifier checks every figure in the answer against it. An indicator value
    that never appeared here cannot survive into the reply.
  * **It never emits an action.** `direction` is bullish/bearish/neutral. There
    is no "buy" anywhere in this module, at any layer, by construction — which
    is what keeps the app's stated posture true while still answering the
    question people actually ask.

Pure and synchronous: `derive_signals` reads a dict that
`services/technicals.py:get_technicals` has already assembled, and touches
neither the network nor the database.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

Direction = Literal["bullish", "bearish", "neutral"]
Timeframe = Literal["intraday", "short", "medium", "long"]
Reliability = Literal["low", "medium", "high"]
Category = Literal["momentum", "trend", "volatility", "level", "event"]
NetBias = Literal["bullish", "bearish", "mixed", "inconclusive"]

DISCLAIMER = (
    "These are conditions observed in past price data, not recommendations. "
    "Technical signals fail often, say nothing about a company's value, and "
    "cannot account for your position, horizon or risk tolerance."
)

# How much a signal counts toward the net bias. A moving-average stack that has
# held for months is worth more than a 14-day oscillator crossing a line, and a
# rule with a weak base rate is worth less than one with a strong one.
TIMEFRAME_WEIGHT: dict[str, float] = {
    "intraday": 0.3,
    "short": 0.5,
    "medium": 0.7,
    "long": 1.0,
}
RELIABILITY_WEIGHT: dict[str, float] = {"low": 0.4, "medium": 0.7, "high": 1.0}

# Total weight at which the evidence is considered to have spoken. Roughly two
# strong long-horizon signals, or three or four short-horizon ones. Below it the
# bias is scaled down, so a lone RSI print cannot read as conviction.
CONVICTION_REFERENCE = 2.0

# Below this the lean is too slight to name a direction.
INCONCLUSIVE_THRESHOLD = 0.15

# When the weaker side carries at least this share of the stronger side's
# weight, the picture is genuinely mixed rather than merely leaning.
MIXED_RATIO = 0.4

# ── Rule thresholds ────────────────────────────────────────────────────────
RSI_OVERSOLD = 30.0
RSI_OVERBOUGHT = 70.0

# Price this far from its 50-day average is stretched. Mean reversion from here
# is a real tendency but a weak one, which is why the rule is graded `low`.
EXTENSION_PERCENT = 10.0

# The average whose crossings chartists actually watch.
TREND_MA = 200
GOLDEN_CROSS_PAIR = (50, 200)


class Signal(BaseModel):
    """One named condition currently true of a ticker's price history."""

    id: str = Field(description="Stable slug, e.g. 'rsi_oversold'.")
    label: str = Field(description="Short human-readable name.")
    category: Category
    direction: Direction = Field(
        description="Which way the condition leans. Never an instruction."
    )
    timeframe: Timeframe = Field(
        description="The horizon this condition speaks to. RSI(14) says days; "
        "a 200-day average says months. Conflating the two is how technical "
        "readings mislead."
    )
    strength: float = Field(ge=0.0, le=1.0, description="How far past the threshold.")
    reliability: Reliability = Field(
        description="Fixed per rule — how much this condition has historically "
        "been worth on its own."
    )
    evidence: dict[str, Any] = Field(
        default_factory=dict,
        description="The numbers this signal was derived from. Every figure the "
        "model is allowed to quote comes from here.",
    )
    rationale: str = Field(description="Deterministic sentence. Not model-written.")
    invalidation: Optional[str] = Field(
        None, description="The observable event that would end this condition."
    )


class SignalSet(BaseModel):
    """Every condition that holds for one ticker, plus how they net out."""

    ticker: str
    as_of: datetime
    signals: list[Signal] = Field(default_factory=list)
    net_bias: NetBias
    bias_score: float = Field(ge=-1.0, le=1.0)
    conflicts: list[str] = Field(
        default_factory=list,
        description="Where the horizons disagree. Present so a narration cannot "
        "quote only the half that suits the question.",
    )
    coverage: dict[str, bool] = Field(
        default_factory=dict,
        description="Which indicator families were computable. A missing family "
        "is a gap in the answer, not an absence of signal.",
    )
    disclaimer: str = DISCLAIMER


# ── Helpers ────────────────────────────────────────────────────────────────


def _usable(block: Any) -> bool:
    """True when an indicator block carries readings rather than an error.

    `get_technicals` reports a failed indicator as `{"error": ...}` in place of
    its readings rather than omitting the key, so presence is not enough.
    """
    return isinstance(block, dict) and "error" not in block


def _num(value: Any) -> Optional[float]:
    """Coerce to float, treating None/non-numeric as absent."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ratio(distance: float, span: float) -> float:
    """Distance past a threshold as a 0..1 strength, saturating at `span`."""
    return round(min(1.0, max(0.0, distance / span)), 4)


def _weight(signal: Signal) -> float:
    return TIMEFRAME_WEIGHT[signal.timeframe] * RELIABILITY_WEIGHT[signal.reliability]


# ── Rules ──────────────────────────────────────────────────────────────────


def _momentum_signals(rsi: dict, macd: dict) -> list[Signal]:
    out: list[Signal] = []

    value = _num(rsi.get("current_rsi")) if _usable(rsi) else None
    period = rsi.get("period", 14) if _usable(rsi) else 14
    if value is not None:
        if value <= RSI_OVERSOLD:
            out.append(Signal(
                id="rsi_oversold",
                label="RSI oversold",
                category="momentum",
                direction="bullish",
                timeframe="short",
                strength=_ratio(RSI_OVERSOLD - value, RSI_OVERSOLD),
                reliability="medium",
                evidence={"rsi": value, "threshold": RSI_OVERSOLD, "period": period},
                rationale=(
                    f"RSI({period}) is {value:.1f}, at or below the {RSI_OVERSOLD:.0f} "
                    f"line conventionally read as oversold. Selling pressure has been "
                    f"one-sided recently, a state that often precedes a bounce and "
                    f"often simply persists."
                ),
                invalidation=(
                    f"Stops applying once RSI({period}) closes back above "
                    f"{RSI_OVERSOLD:.0f}."
                ),
            ))
        elif value >= RSI_OVERBOUGHT:
            out.append(Signal(
                id="rsi_overbought",
                label="RSI overbought",
                category="momentum",
                direction="bearish",
                timeframe="short",
                strength=_ratio(value - RSI_OVERBOUGHT, 100.0 - RSI_OVERBOUGHT),
                reliability="medium",
                evidence={"rsi": value, "threshold": RSI_OVERBOUGHT, "period": period},
                rationale=(
                    f"RSI({period}) is {value:.1f}, at or above the "
                    f"{RSI_OVERBOUGHT:.0f} line conventionally read as overbought. "
                    f"Strong trends can hold this reading for weeks, so on its own it "
                    f"marks stretched momentum rather than exhaustion."
                ),
                invalidation=(
                    f"Stops applying once RSI({period}) closes back below "
                    f"{RSI_OVERBOUGHT:.0f}."
                ),
            ))

    if not _usable(macd):
        return out

    histogram = _num(macd.get("histogram"))
    macd_line = _num(macd.get("macd"))
    signal_line = _num(macd.get("signal"))
    evidence = {"macd": macd_line, "signal": signal_line, "histogram": histogram}

    if macd.get("bullish_crossover"):
        out.append(Signal(
            id="macd_bullish_cross",
            label="MACD crossed up",
            category="momentum",
            direction="bullish",
            timeframe="short",
            strength=0.6,
            reliability="medium",
            evidence=evidence,
            rationale=(
                "The MACD line crossed above its signal line on the latest bar, so "
                "the histogram has just turned positive. This marks a shift in "
                "short-term momentum, and it whipsaws in sideways markets."
            ),
            invalidation="Stops applying if the histogram turns negative again.",
        ))
    elif macd.get("bearish_crossover"):
        out.append(Signal(
            id="macd_bearish_cross",
            label="MACD crossed down",
            category="momentum",
            direction="bearish",
            timeframe="short",
            strength=0.6,
            reliability="medium",
            evidence=evidence,
            rationale=(
                "The MACD line crossed below its signal line on the latest bar, so "
                "the histogram has just turned negative. This marks a shift in "
                "short-term momentum, and it whipsaws in sideways markets."
            ),
            invalidation="Stops applying if the histogram turns positive again.",
        ))
    elif histogram is not None and histogram != 0:
        # Only when there is no crossover — a fresh cross already says this, and
        # counting both would let one indicator vote twice in the net bias.
        positive = histogram > 0
        out.append(Signal(
            id="macd_momentum_positive" if positive else "macd_momentum_negative",
            label="MACD histogram positive" if positive else "MACD histogram negative",
            category="momentum",
            direction="bullish" if positive else "bearish",
            timeframe="short",
            strength=0.3,
            reliability="low",
            evidence=evidence,
            rationale=(
                f"The MACD histogram is {histogram:+.3f}, so the faster average sits "
                f"{'above' if positive else 'below'} the slower one. This describes "
                f"the momentum already in place rather than a change in it."
            ),
            invalidation="Stops applying when the histogram changes sign.",
        ))

    return out


def _trend_signals(ladder: dict) -> list[Signal]:
    if not _usable(ladder):
        return []

    out: list[Signal] = []
    levels = ladder.get("levels") or []
    price = _num(ladder.get("current_price"))
    alignment = ladder.get("alignment")

    if alignment in ("bullish", "bearish"):
        bullish = alignment == "bullish"
        out.append(Signal(
            id=f"ma_stack_{alignment}",
            label=f"Moving averages stacked {alignment}ly",
            category="trend",
            direction="bullish" if bullish else "bearish",
            timeframe="long",
            strength=0.7,
            reliability="medium",
            evidence={
                "alignment": alignment,
                "above_count": ladder.get("above_count"),
                "total_count": ladder.get("total_count"),
            },
            rationale=(
                f"Every moving average sits in {'descending' if bullish else 'ascending'} "
                f"order from fastest to slowest, the textbook shape of an established "
                f"{'up' if bullish else 'down'}trend. Ladders unwind slowly, so this "
                f"describes months rather than days."
            ),
            invalidation="Stops applying once the averages lose that order.",
        ))

    trend_level = next(
        (lvl for lvl in levels if lvl.get("window") == TREND_MA and lvl.get("available")),
        None,
    )
    if trend_level is not None:
        sma = _num(trend_level.get("sma"))
        distance = _num(trend_level.get("distance_percent"))
        above = bool(trend_level.get("above"))
        if sma is not None:
            out.append(Signal(
                id=f"price_{'above' if above else 'below'}_ma{TREND_MA}",
                label=f"Price {'above' if above else 'below'} the {TREND_MA}-day average",
                category="trend",
                direction="bullish" if above else "bearish",
                timeframe="long",
                strength=_ratio(abs(distance or 0.0), 20.0),
                reliability="medium",
                evidence={
                    "price": price,
                    f"sma_{TREND_MA}": sma,
                    "distance_percent": distance,
                },
                rationale=(
                    f"Price is {abs(distance or 0.0):.1f}% "
                    f"{'above' if above else 'below'} its {TREND_MA}-day average "
                    f"({sma:.2f}), the line most often used to separate a long-term "
                    f"uptrend from a downtrend."
                ),
                invalidation=(
                    f"Stops applying on a close back "
                    f"{'below' if above else 'above'} {sma:.2f}."
                ),
            ))

    for cross in ladder.get("crosses") or []:
        if (cross.get("fast"), cross.get("slow")) != GOLDEN_CROSS_PAIR:
            continue
        bullish = cross.get("type") == "bullish"
        out.append(Signal(
            id="golden_cross" if bullish else "death_cross",
            label="Golden cross" if bullish else "Death cross",
            category="trend",
            direction="bullish" if bullish else "bearish",
            timeframe="long",
            strength=0.8,
            reliability="medium",
            evidence={"fast": GOLDEN_CROSS_PAIR[0], "slow": GOLDEN_CROSS_PAIR[1]},
            rationale=(
                f"The {GOLDEN_CROSS_PAIR[0]}-day average crossed "
                f"{'above' if bullish else 'below'} the {GOLDEN_CROSS_PAIR[1]}-day "
                f"average on the latest bar. It is a widely watched marker of a "
                f"trend change, and because both inputs are lagging it confirms a "
                f"move that has already happened."
            ),
            invalidation="Stops applying if the two averages cross back.",
        ))

    fifty = next(
        (lvl for lvl in levels if lvl.get("window") == 50 and lvl.get("available")),
        None,
    )
    if fifty is not None:
        distance = _num(fifty.get("distance_percent"))
        if distance is not None and abs(distance) > EXTENSION_PERCENT:
            stretched_up = distance > 0
            # Deliberately opposite the extension: this is a mean-reversion
            # observation, so price far above its average leans bearish.
            out.append(Signal(
                id="extended_from_ma50",
                label="Stretched from the 50-day average",
                category="trend",
                direction="bearish" if stretched_up else "bullish",
                timeframe="short",
                strength=_ratio(abs(distance) - EXTENSION_PERCENT, 20.0),
                reliability="low",
                evidence={"distance_percent": distance, "threshold": EXTENSION_PERCENT},
                rationale=(
                    f"Price sits {abs(distance):.1f}% "
                    f"{'above' if stretched_up else 'below'} its 50-day average. "
                    f"Gaps this wide tend to close, though they can widen much "
                    f"further first and the tendency is weak."
                ),
                invalidation=(
                    f"Stops applying once price is back within "
                    f"{EXTENSION_PERCENT:.0f}% of the 50-day average."
                ),
            ))

    return out


def _level_signals(drawdown: dict) -> list[Signal]:
    """Where price sits inside its own 52-week range."""
    if not _usable(drawdown):
        return []

    from_high = _num(drawdown.get("from_high_percent"))
    status = drawdown.get("status")
    if from_high is None or status is None:
        return []

    evidence = {
        "from_high_percent": from_high,
        "high_52w": _num(drawdown.get("high_52w")),
        "range_position": _num(drawdown.get("range_position")),
    }

    if status == "at_high":
        return [Signal(
            id="near_52w_high",
            label="Near the 52-week high",
            category="level",
            direction="bullish",
            timeframe="medium",
            strength=0.5,
            reliability="medium",
            evidence=evidence,
            rationale=(
                f"Price is within {abs(from_high):.1f}% of its 52-week high. Everyone "
                f"holding the stock is in profit, so there is no overhead supply from "
                f"buyers waiting to get back to even."
            ),
            invalidation="Stops applying once price falls more than 2% off the high.",
        )]

    if status == "bear_market":
        return [Signal(
            id="deep_drawdown",
            label="Down more than 20% from the high",
            category="level",
            direction="bearish",
            timeframe="long",
            strength=_ratio(abs(from_high) - 20.0, 30.0),
            reliability="medium",
            evidence=evidence,
            rationale=(
                f"Price is {abs(from_high):.1f}% below its 52-week high, past the 20% "
                f"mark conventionally called a bear market. Falls this deep usually "
                f"reflect a changed outlook rather than noise."
            ),
            invalidation="Stops applying once price recovers to within 20% of the high.",
        )]

    if status == "correction":
        return [Signal(
            id="in_correction",
            label="In a correction",
            category="level",
            direction="bearish",
            timeframe="medium",
            strength=_ratio(abs(from_high) - 10.0, 10.0),
            reliability="low",
            evidence=evidence,
            rationale=(
                f"Price is {abs(from_high):.1f}% below its 52-week high, in the 10–20% "
                f"band conventionally called a correction. Drops of this size are "
                f"common and resolve in both directions."
            ),
            invalidation="Stops applying once price recovers to within 10% of the high.",
        )]

    return []


def _event_signals(ticker: str, events: Optional[dict]) -> list[Signal]:
    """Scheduled events that dominate whatever the indicators say."""
    if not events:
        return []

    for entry in events.get("earnings") or []:
        if str(entry.get("symbol", "")).upper() != ticker:
            continue
        when = entry.get("date")
        return [Signal(
            id="earnings_upcoming",
            label="Earnings due",
            category="event",
            direction="neutral",
            timeframe="short",
            strength=1.0,
            reliability="high",
            evidence={"date": when, "symbol": ticker},
            rationale=(
                f"{ticker} reports earnings on {when}. A scheduled release resets the "
                f"information every technical reading here is based on, and price "
                f"routinely gaps through the levels below."
            ),
            invalidation="Stops applying once the report is out.",
        )]

    return []


# ── Aggregation ────────────────────────────────────────────────────────────


def _score(signals: list[Signal]) -> float:
    """Net direction, scaled by how much evidence there is.

    Two quantities, deliberately separated. *Agreement* is which way the signals
    lean, on -1..1, and is blind to how many there are. *Conviction* is the total
    weight behind them, capped at 1. Their product means one weak reading cannot
    register as a strong lean, while several aligned ones can.

    Neutral signals are excluded from both: an earnings date is context, not a
    direction, and letting it into the denominator would dilute real signal.
    """
    directional = [s for s in signals if s.direction != "neutral"]
    total = sum(s.strength * _weight(s) for s in directional)
    if total <= 0:
        return 0.0

    net = sum(
        (1 if s.direction == "bullish" else -1) * s.strength * _weight(s)
        for s in directional
    )
    agreement = net / total
    conviction = min(1.0, total / CONVICTION_REFERENCE)
    return round(max(-1.0, min(1.0, agreement * conviction)), 4)


def _net_bias(signals: list[Signal], score: float) -> NetBias:
    bull = sum(s.strength * _weight(s) for s in signals if s.direction == "bullish")
    bear = sum(s.strength * _weight(s) for s in signals if s.direction == "bearish")

    if bull <= 0 and bear <= 0:
        return "inconclusive"

    weaker, stronger = min(bull, bear), max(bull, bear)
    if weaker > 0 and weaker / stronger >= MIXED_RATIO:
        return "mixed"
    if abs(score) < INCONCLUSIVE_THRESHOLD:
        return "inconclusive"
    return "bullish" if score > 0 else "bearish"


_HORIZON_NAME = {
    "intraday": "intraday",
    "short": "short-term",
    "medium": "medium-term",
    "long": "long-term",
}
_HORIZON_ORDER = ["intraday", "short", "medium", "long"]


def _conflicts(signals: list[Signal]) -> list[str]:
    """Sentences naming where the horizons disagree.

    Present so a narration cannot quote only the half that suits the question —
    the model is required to carry these through.
    """
    leaning: dict[str, float] = {}
    for signal in signals:
        if signal.direction == "neutral":
            continue
        sign = 1 if signal.direction == "bullish" else -1
        leaning[signal.timeframe] = (
            leaning.get(signal.timeframe, 0.0) + sign * signal.strength * _weight(signal)
        )

    present = [tf for tf in _HORIZON_ORDER if leaning.get(tf)]
    out: list[str] = []
    for i, first in enumerate(present):
        for second in present[i + 1:]:
            if (leaning[first] > 0) == (leaning[second] > 0):
                continue
            bull, bear = (
                (first, second) if leaning[first] > 0 else (second, first)
            )
            out.append(
                f"{_HORIZON_NAME[bull]} signals lean bullish while "
                f"{_HORIZON_NAME[bear]} signals lean bearish"
            )
    return out


def derive_signals(
    technicals: dict,
    *,
    ticker: Optional[str] = None,
    events: Optional[dict] = None,
) -> SignalSet:
    """Name every technical condition currently true of one ticker.

    `technicals` is the dict returned by `services/technicals.py:get_technicals`.
    Any indicator inside it may be `{"error": ...}` instead of readings; each
    family degrades independently and is reported in `coverage` so the caller can
    say what it could not see rather than implying there was nothing to see.

    `events` is an optional `market_data.get_events` payload. Pure — same input
    always yields the same output.
    """
    symbol = (ticker or technicals.get("symbol") or "").upper()

    rsi = technicals.get("rsi") or {}
    macd = technicals.get("macd") or {}
    ladder = technicals.get("moving_averages") or {}
    drawdown = technicals.get("drawdown") or {}

    signals = [
        *_momentum_signals(rsi, macd),
        *_trend_signals(ladder),
        *_level_signals(drawdown),
        *_event_signals(symbol, events),
    ]

    score = _score(signals)
    as_of = technicals.get("as_of")
    if isinstance(as_of, str):
        try:
            parsed = datetime.fromisoformat(as_of)
        except ValueError:
            parsed = datetime.now(timezone.utc)
    elif isinstance(as_of, datetime):
        parsed = as_of
    else:
        parsed = datetime.now(timezone.utc)

    return SignalSet(
        ticker=symbol,
        as_of=parsed,
        signals=signals,
        net_bias=_net_bias(signals, score),
        bias_score=score,
        conflicts=_conflicts(signals),
        coverage={
            "momentum": _usable(rsi) or _usable(macd),
            "trend": _usable(ladder),
            "level": _usable(drawdown),
            "event": bool(events),
        },
    )
