"""Crowd rules: thresholds at their boundaries, and never an instruction.

Mirrors tests/unit/test_signals.py: every declared rule must be reachable,
every sentence it can produce must pass the advice check in both modes, and
`direction` is the only lean a signal can carry.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.agents.base.guardrails import check_investment_advice
from app.services import social_signals as ss
from app.services.social.models import CrowdTags, RedditAttention, SocialPositioning


def reddit(**kw) -> RedditAttention:
    base = dict(source="apewisdom", symbol="PLTR", rank=30, rank_24h_ago=32, rank_change=2,
                mentions=40, mentions_24h_ago=38, history_days=0, attention_z=None)
    base.update(kw)
    return RedditAttention(**base)


def crowd(bullish: int, bearish: int, untagged: int = 5) -> CrowdTags:
    tagged = bullish + bearish
    return CrowdTags(source="stocktwits", symbol="PLTR", bullish=bullish, bearish=bearish,
                     untagged=untagged, sampled=tagged + untagged,
                     bull_pct=round(bullish / tagged * 100, 1) if tagged else None)


def ids(signal_set) -> set[str]:
    return {s.id for s in signal_set.signals}


def derive(**kw):
    return ss.derive_social_signals("PLTR", **kw)


# ── Attention ───────────────────────────────────────────────────────────────


def test_a_quiet_ticker_has_no_conditions():
    result = derive(reddit=reddit(), crowd=crowd(10, 5), positioning=SocialPositioning())
    assert result.signals == [] and result.net_bias == "inconclusive"


@pytest.mark.parametrize("z, fires", [(1.99, False), (2.0, True), (4.5, True)])
def test_mention_spike_threshold(z, fires):
    result = derive(reddit=reddit(attention_z=z, history_days=10))
    assert ("mention_spike" in ids(result)) is fires


def test_a_spike_needs_enough_history():
    result = derive(reddit=reddit(attention_z=5.0, history_days=6))
    assert "mention_spike" not in ids(result)


@pytest.mark.parametrize("mentions, before, fires", [
    (75, 25, True),      # exactly 3x, at the floor
    (74, 25, False),     # just under 3x
    (24, 8, False),      # 3x but under MIN_MENTIONS
])
def test_surge_fallback_threshold(mentions, before, fires):
    result = derive(reddit=reddit(mentions=mentions, mentions_24h_ago=before))
    assert ("mention_surge_24h" in ids(result)) is fires


def test_the_surge_fallback_steps_aside_once_history_exists():
    result = derive(reddit=reddit(mentions=300, mentions_24h_ago=10, attention_z=0.5,
                                  history_days=14))
    assert "mention_surge_24h" not in ids(result) and "mention_spike" not in ids(result)


@pytest.mark.parametrize("rank, before, change, fires", [
    (40, 55, 15, True),
    (40, 54, 14, False),
    (20, 21, 1, True),        # new to the top 20
    (21, 25, 4, False),
    (18, None, None, True),   # new to the board, straight into the top 20
])
def test_rank_climb(rank, before, change, fires):
    result = derive(reddit=reddit(rank=rank, rank_24h_ago=before, rank_change=change))
    assert ("rank_climb" in ids(result)) is fires


@pytest.mark.parametrize("before_rank, mentions, before, fires", [
    (10, 50, 100, True),
    (10, 51, 100, False),
    (21, 10, 100, False),     # was not in the top 20
])
def test_attention_fading(before_rank, mentions, before, fires):
    result = derive(reddit=reddit(rank=25, rank_24h_ago=before_rank, rank_change=before_rank - 25,
                                  mentions=mentions, mentions_24h_ago=before))
    assert ("attention_fading" in ids(result)) is fires


def test_attention_never_has_a_direction():
    result = derive(reddit=reddit(rank=3, rank_24h_ago=60, rank_change=57, mentions=900,
                                  mentions_24h_ago=50, attention_z=6, history_days=30))
    assert result.signals
    assert {s.direction for s in result.signals} == {"neutral"}
    assert result.net_bias == "inconclusive"


def test_an_unavailable_board_is_a_coverage_gap_not_silence():
    result = derive(reddit=RedditAttention(source="apewisdom", symbol="PLTR", available=False))
    assert result.coverage["reddit"] is False and result.signals == []


# ── Crowd tags ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("bull, bear, expected", [
    (12, 3, "crowd_bullish"),     # 80.0%
    (11, 4, None),                # 73.3% — the resting level, not a lean
    (9, 11, "crowd_bearish"),     # 45.0%
    (10, 10, None),               # 50% — below even odds is required on this scale
])
def test_crowd_thresholds_are_asymmetric(bull, bear, expected):
    found = ids(derive(crowd=crowd(bull, bear))) & {"crowd_bullish", "crowd_bearish"}
    assert found == ({expected} if expected else set())


def test_a_small_tagged_sample_says_nothing():
    assert derive(crowd=crowd(14, 0)).signals == []


def test_crowd_direction_is_the_only_lean_and_a_weak_one():
    """One low-reliability reading leans the score but cannot name a bias alone.

    The shared scorer (signals._score) scales by conviction, so a lone
    self-tagged split never reads as "Leaning bullish" on its own.
    """
    bullish = derive(crowd=crowd(18, 2))
    bearish = derive(crowd=crowd(3, 17))
    assert bullish.bias_score > 0 > bearish.bias_score
    assert bullish.net_bias == bearish.net_bias == "inconclusive"


def test_one_sided_needs_attention_too():
    assert "crowd_one_sided" not in ids(derive(reddit=reddit(), crowd=crowd(19, 1)))
    surging = reddit(mentions=90, mentions_24h_ago=20)
    assert "crowd_one_sided" in ids(derive(reddit=surging, crowd=crowd(19, 1)))


def test_evidence_is_in_quotable_units():
    signal = next(s for s in derive(crowd=crowd(18, 2)).signals if s.id == "crowd_bullish")
    assert signal.evidence["bull_pct"] == 90.0 and signal.evidence["tagged"] == 20


# ── Positioning ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("spf, fires", [(19.99, False), (20.0, True), (None, False)])
def test_short_interest_threshold(spf, fires):
    result = derive(positioning=SocialPositioning(short_percent_float=spf))
    assert ("short_interest_high" in ids(result)) is fires


# ── Contract ────────────────────────────────────────────────────────────────


def _everything() -> list:
    """Inputs that, between them, fire every declared rule."""
    return [
        derive(reddit=reddit(attention_z=3.0, history_days=20, rank=5, rank_24h_ago=40,
                             rank_change=35, mentions=200, mentions_24h_ago=20),
               crowd=crowd(19, 1), positioning=SocialPositioning(short_percent_float=31.0)),
        derive(reddit=reddit(mentions=90, mentions_24h_ago=20)),
        derive(reddit=reddit(rank=30, rank_24h_ago=5, rank_change=-25, mentions=40,
                             mentions_24h_ago=100), crowd=crowd(2, 18)),
    ]


def test_every_declared_rule_can_fire():
    fired = set().union(*(ids(s) for s in _everything()))
    assert fired == ss.DECLARED_IDS


@pytest.mark.parametrize("mode", ["strict", "chat"])
def test_no_rationale_or_invalidation_reads_as_advice(mode):
    for signal_set in _everything():
        for signal in signal_set.signals:
            for text in (signal.rationale, signal.invalidation or ""):
                assert check_investment_advice(text, mode=mode) == [], (signal.id, text)
        assert check_investment_advice(signal_set.disclaimer, mode=mode) == []


def test_no_signal_carries_an_action():
    for signal_set in _everything():
        for signal in signal_set.signals:
            assert signal.direction in {"bullish", "bearish", "neutral"}
            assert signal.category == "social"
            assert not hasattr(signal, "action")


def test_the_module_never_spells_an_action_verb():
    """Same guarantee as signals.py: no buy, sell or hold anywhere in the source."""
    source = Path(ss.__file__).read_text()
    assert not re.search(r"\b(buy|sell|hold)\b", source, re.IGNORECASE)


def test_social_sets_carry_their_own_disclaimer():
    assert derive().disclaimer == ss.SOCIAL_DISCLAIMER


def test_trending_badges_use_attention_rules_only():
    from app.services.social.models import TrendingRow

    row = TrendingRow(symbol="AMD", name="AMD", rank=1, rank_24h_ago=6, rank_change=5,
                      mentions=189, mentions_24h_ago=50)
    assert ss.trending_signal_ids(row) == ["mention_surge_24h"]
