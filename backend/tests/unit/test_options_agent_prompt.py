"""The options prompt against the advice guardrail — no network, no model.

The guardrails node runs a regex advice detector over every agent's `findings`,
and ordinary options prose trips it constantly: describing assignment naturally
produces "you must sell the shares at the strike", and describing a breakeven
naturally produces "a price target of $X".

The prompt handles that by supplying required phrasings rather than only
forbidding wrong ones. These tests hold that contract in place. They are cheap
insurance on a prompt edit — the failure mode otherwise is a slow drip of
false-positive `investment_advice` flags that nobody traces back to a wording
change.
"""

from __future__ import annotations

import pytest

from app.agents.base.guardrails import check_investment_advice
from app.agents.options.agent import _STRUCTURAL_CAVEATS
from app.agents.options.prompts import PROMPT_VERSION, SYSTEM_PROMPT, TOOL_LIST


def test_the_example_finding_would_not_trip_the_guardrail():
    """The model imitates the example, so the example has to be clean itself."""
    _, _, example = SYSTEM_PROMPT.partition("## Example finding (abbreviated)")

    assert example, "the prompt should carry a worked example to imitate"
    assert check_investment_advice(example) == []


@pytest.mark.parametrize(
    "phrasing",
    [
        "Assignment obligates the seller to purchase 100 shares at the strike.",
        "The breakeven at expiry is $93.45.",
        "The position retains its full credit above $93.45.",
        "The highest-ranked candidate by annualised yield times probability of "
        "profit is the September 19 $95 put.",
        "A contract that passes the filters at the 0.22 delta collects $1.55.",
        "The model-implied probability of finishing above the breakeven is 78% "
        "under a risk-neutral lognormal assumption.",
    ],
)
def test_required_phrasings_pass_the_advice_detector(phrasing):
    assert check_investment_advice(phrasing) == [], (
        "a phrasing the prompt mandates must not trip the guardrail"
    )


@pytest.mark.parametrize(
    "phrasing",
    [
        "You must sell the shares at $95 if assigned.",
        "This is a good time to enter the position.",
        "We recommend selling the September put.",
        "A price target of $120 is reasonable.",
    ],
)
def test_the_natural_phrasings_really_would_have_tripped_it(phrasing):
    """Proves the substitutions are load-bearing rather than superstition."""
    assert check_investment_advice(phrasing), (
        "if this stops flagging, the prompt's phrasing rules can be relaxed"
    )


def test_prompt_forbids_the_second_person():
    """One rule that removes three of the detector's patterns at once."""
    assert 'Never use the word "you"' in SYSTEM_PROMPT


def test_the_structural_caveats_are_guaranteed_by_code_not_the_prompt():
    """They are appended after the model runs, so they cannot go missing.

    They used to be required in the prompt as well, which meant a reader saw
    each point twice in slightly different wording. The guarantee lives in one
    place now; this asserts it is the reliable one.
    """
    joined = " ".join(_STRUCTURAL_CAVEATS).lower()

    assert "probability of profit is not a high expected return" in joined
    assert "risk-neutral" in joined
    assert "early assignment" in joined


def test_prompt_does_not_ask_the_model_to_repeat_them():
    lowered = SYSTEM_PROMPT.lower()

    assert "do not repeat those" in lowered
    assert "appended automatically" in lowered


def test_prompt_pins_the_volatility_direction():
    """Left to infer it, the model got this backwards on live data.

    Implied below realized means the seller is underpaid, which favours the
    buyer. The explainer described it as leading to higher returns for sellers
    — the single most damaging sentence a premium-selling screen could produce.
    """
    lowered = SYSTEM_PROMPT.lower()

    assert "easy to invert" in lowered
    assert "favours the buyer" in lowered
    assert "do not describe cheap premium as an opportunity" in lowered


def test_prompt_metadata_matches_the_agent():
    assert PROMPT_VERSION == "v1"
    assert TOOL_LIST == ["get_options_snapshot", "screen_option_strategies"]
