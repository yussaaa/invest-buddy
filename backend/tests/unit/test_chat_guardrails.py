"""Unit tests for the two-tier advice guardrail and numeric grounding.

Two boundaries are pinned here, and they pull in opposite directions.

The first is that chat mode must catch what a written report never provokes:
capitulation under pressure. A conversation gets argued with, and "fine, but
would you buy it?" is the follow-up the agent will see most.

The second is that the vocabulary the feature is built on must stay usable.
Describing a condition in the third person — "RSI is oversold, which sometimes
precedes a bounce" — is the entire product. A future pattern that bans it would
pass every other test in the suite and silently gut the feature, so it is
asserted explicitly below.
"""

from __future__ import annotations

import pytest

from app.agents.base.guardrails import (
    check_investment_advice,
    check_numeric_grounding,
)

# Phrasings the agent must remain free to use. Third-person descriptions of a
# condition, hedged, with no instruction to anyone.
PERMITTED = [
    "RSI is oversold at 28.4, which sometimes precedes a bounce.",
    "This is a bullish setup on the daily chart, though the trend disagrees.",
    "Buyers stepped in near 180 on the last three tests of that level.",
    "The moving averages are stacked bullishly, which describes months rather than days.",
    "Momentum is stretched to the downside.",
    "Price sits 12.4% below its 200-day average, conventionally read as a downtrend.",
    "Selling pressure has been one-sided recently.",
    "A close back above 30 would end the oversold reading.",
    "The condition often resolves upward and often simply persists.",
    "Historically, this pattern has resolved in both directions.",
]

# Phrasings that are advice however they are dressed up.
BLOCKED_EVERYWHERE = [
    "You should buy AAPL before earnings.",
    "This is a strong buy at current levels.",
    "I recommend buying here.",
    "Analysts say the price target of $250 is conservative.",
    "This stock is a buy.",
    "Now is the time to buy.",
    "It is guaranteed to return 20% this year.",
    "The stock is clearly undervalued.",
    "Shares will surge to $400 by December.",
    "This is an excellent buying opportunity.",
]

# Capitulation — what an interactive agent says on the fourth follow-up.
BLOCKED_IN_CHAT_ONLY = [
    "Honestly, I'd be buying here.",
    "If I were you, I'd wait for the retest.",
    "Personally, buy — the setup is clean.",
    "My recommendation is to wait for confirmation.",
    "It's worth buying at this level.",
    "I would avoid it now.",
    "Time to go long.",
    "You could load up while it's cheap.",
    "Take your profits before earnings.",
    "Cut your losses here.",
    "In your position, I'd hold off.",
    "Should you buy? Probably.",
]


# ── Mode behaviour ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("text", BLOCKED_EVERYWHERE)
def test_direct_advice_is_caught_in_both_modes(text):
    assert check_investment_advice(text, mode="strict")
    assert check_investment_advice(text, mode="chat")


@pytest.mark.parametrize("text", BLOCKED_IN_CHAT_ONLY)
def test_capitulation_is_caught_in_chat_mode(text):
    assert check_investment_advice(text, mode="chat"), f"chat mode let through: {text}"


@pytest.mark.parametrize("text", PERMITTED)
def test_describing_a_condition_is_permitted_in_both_modes(text):
    """The vocabulary the feature is built on. If this fails, the feature is gone."""
    assert check_investment_advice(text, mode="strict") == []
    assert check_investment_advice(text, mode="chat") == []


def test_chat_mode_is_a_superset_never_a_relaxation():
    """Chat is stricter than analysis, on every input, by construction."""
    for text in PERMITTED + BLOCKED_EVERYWHERE + BLOCKED_IN_CHAT_ONLY:
        strict = check_investment_advice(text, mode="strict")
        chat = check_investment_advice(text, mode="chat")
        assert len(chat) >= len(strict), f"chat mode was more permissive on: {text}"


def test_strict_is_the_default_so_existing_callers_are_untouched():
    text = "You should buy AAPL."

    assert check_investment_advice(text) == check_investment_advice(text, mode="strict")


def test_flagged_hits_carry_surrounding_context_for_audit():
    hits = check_investment_advice("Given the setup, you should buy now.", mode="chat")

    assert hits
    assert "you should buy" in hits[0].lower()


# ── Numeric grounding ───────────────────────────────────────────────────────


EVIDENCE = {
    "rsi": 28.44,
    "threshold": 30,
    "period": 14,
    "moving_averages": {"sma_200": 195.32, "distance_percent": -4.18},
    "levels": [{"window": 50, "sma": 201.75}],
}


def test_a_quoted_reading_is_grounded():
    assert check_numeric_grounding("RSI is at 28.44 today.", EVIDENCE) == []


def test_a_rounded_reading_is_still_grounded():
    """A model that says 28.4, or 28, is describing the same reading."""
    assert check_numeric_grounding("RSI is 28.4.", EVIDENCE) == []
    assert check_numeric_grounding("RSI is about 28.", EVIDENCE) == []


def test_an_invented_reading_is_caught():
    assert check_numeric_grounding("RSI is at 41.", EVIDENCE) == ["41"]


def test_a_plausible_but_wrong_price_is_caught():
    assert check_numeric_grounding("The 200-day sits at 188.10.", EVIDENCE) == ["188.10"]


def test_nested_and_listed_evidence_is_reachable():
    assert check_numeric_grounding("The 50-day is 201.75.", EVIDENCE) == []
    assert check_numeric_grounding("It is 4.18% below.", EVIDENCE) == []


def test_currency_and_percent_formatting_is_understood():
    assert check_numeric_grounding("Around $195.32, or -4.18%.", EVIDENCE) == []


def test_small_counts_are_not_treated_as_claims():
    text = "Three of the 5 averages agree, across 2 horizons."

    assert check_numeric_grounding(text, EVIDENCE) == []


def test_years_are_not_treated_as_claims():
    assert check_numeric_grounding("The 2020 selloff was deeper.", EVIDENCE) == []


def test_every_ungrounded_figure_is_reported():
    hits = check_numeric_grounding("RSI 41 and the average at 188.10.", EVIDENCE)

    assert set(hits) == {"41", "188.10"}


def test_text_without_numbers_is_trivially_grounded():
    assert check_numeric_grounding("Momentum is stretched to the downside.", EVIDENCE) == []


def test_empty_evidence_flags_any_real_figure():
    """A reply built on nothing must not read as grounded."""
    assert check_numeric_grounding("RSI is 28.44.", {}) == ["28.44"]


def test_a_negative_reading_grounds_the_magnitude_quoted_in_prose():
    """Evidence holds -4.18; the sentence says "4.18% below". Same reading."""
    assert check_numeric_grounding("It sits 4.18% below the average.", EVIDENCE) == []


def test_booleans_are_not_collected_as_numbers():
    """A True would otherwise enter the evidence set as 1.0 and ground claims near it."""
    assert check_numeric_grounding("It is 1.005 standard deviations out.",
                                   {"bullish_crossover": True}) == ["1.005"]


def test_a_signal_set_can_be_passed_as_evidence_directly():
    """The graph hands it whole rather than curating a subset."""
    from app.services.signals import derive_signals

    payload = {
        "symbol": "TEST",
        "rsi": {"ticker": "TEST", "period": 14, "current_rsi": 22.0, "zone": "oversold"},
        "macd": {"error": "x"},
        "moving_averages": {"error": "x"},
        "drawdown": {"error": "x"},
        "as_of": "2026-08-06T12:00:00+00:00",
    }
    signal_set = derive_signals(payload)

    assert check_numeric_grounding("RSI is 22.", signal_set.model_dump()) == []
    assert check_numeric_grounding("RSI is 55.", signal_set.model_dump()) == ["55"]
