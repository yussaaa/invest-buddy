"""Unit tests for the valuation explainer's guard — no network, no model.

What matters here is the fallback. A model reply that names a verdict or
invents a number must never reach the panel, and what replaces it must itself
pass both checks — otherwise the guard would swap one bad paragraph for another.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import valuation_explain as ve
from app.services.valuation_math import (
    Assumptions,
    dcf_value,
    implied_growth,
    scenario_band,
    sensitivity_grid,
)

BASE_FCF = 67.0e9
SHARES = 7.43e9
PRICE = 420.0
ASSUMPTIONS = Assumptions(
    initial_growth=0.12, terminal_growth=0.025, discount_rate=0.09, years=10
)


def valuation_payload(price: float | None = PRICE) -> dict:
    """An MSFT-shaped payload in the exact shape valuation.get_valuation returns."""
    return {
        "symbol": "MSFT",
        "assumptions": {
            "initial_growth": ASSUMPTIONS.initial_growth,
            "initial_growth_source": "derived",
            "terminal_growth": ASSUMPTIONS.terminal_growth,
            "discount_rate": ASSUMPTIONS.discount_rate,
            "discount_rate_source": "derived",
            "years": ASSUMPTIONS.years,
        },
        "inputs": {
            "price": price,
            "fcf_base_used": BASE_FCF,
            "fcf_base": "ttm",
            "fcf_dispersion": 0.24,
        },
        "implied_growth": (
            implied_growth(price, BASE_FCF, SHARES, ASSUMPTIONS) if price else None
        ),
        "base_case": dcf_value(BASE_FCF, SHARES, ASSUMPTIONS),
        "scenarios": scenario_band(BASE_FCF, SHARES, ASSUMPTIONS),
        "sensitivity": sensitivity_grid(BASE_FCF, SHARES, ASSUMPTIONS),
    }


# ── the brief ────────────────────────────────────────────────────────────────


def test_the_brief_puts_numbers_in_the_units_prose_quotes_them_in():
    b = ve.brief(valuation_payload())

    assert b["assumptions"]["discount_rate_percent"] == 9.0
    assert b["assumptions"]["terminal_growth_percent"] == 2.5
    assert b["fcf_base_billions"] == 67.0
    assert 0 < b["implied_growth_percent"] < 100
    assert b["sensitivity_range"]["low"] < b["sensitivity_range"]["high"]


# ── the fallback must pass the same checks as the model ─────────────────────


@pytest.mark.parametrize("price", [PRICE, 150.0, 900.0, None])
def test_the_templated_explanation_passes_both_checks(price):
    b = ve.brief(valuation_payload(price))
    text = ve.templated_explanation(b)

    assert text
    assert ve.review(text, b) == [], ve.review(text, b)


# ── the guard, end to end ────────────────────────────────────────────────────


class FakeProvider:
    def __init__(self, reply: str):
        self.reply = reply
        self.calls = 0

    async def complete(self, **_kwargs):
        self.calls += 1
        return SimpleNamespace(content=self.reply, model="fake-model")


@pytest.fixture
def wired(monkeypatch):
    """Everything explain_valuation reaches for, minus the network and Redis."""
    async def fake_valuation(symbol, **_overrides):
        return valuation_payload()

    async def passthrough(_key, _ttl, fetch, should_cache=None):
        return await fetch()

    monkeypatch.setattr(ve.valuation, "get_valuation", fake_valuation)
    monkeypatch.setattr(ve, "cached_async", passthrough)
    monkeypatch.setattr(ve, "has_llm_credentials", lambda _s: True)

    def use(reply: str) -> FakeProvider:
        provider = FakeProvider(reply)
        monkeypatch.setattr(ve, "get_provider", lambda: provider)
        return provider

    return use


@pytest.mark.parametrize("reply", [
    "On these assumptions MSFT appears undervalued.",
    "The stock looks cheap against its base case.",
    "Fair value is $480 per share.",
    # Grounding, not vocabulary: a figure the valuation does not contain.
    "The price implies 37.7% growth for a decade.",
])
async def test_a_reply_that_fails_either_check_is_replaced(wired, reply):
    wired(reply)

    result = await ve.explain_valuation("msft")

    assert result["available"] is True
    assert result["guarded"] is True
    assert result["explanation"] != reply
    assert result["explanation"] == ve.templated_explanation(ve.brief(valuation_payload()))


async def test_a_clean_reply_is_shown_as_written(wired):
    b = ve.brief(valuation_payload())
    reply = (
        f"At ${b['price']:.2f}, the price implies about {b['implied_growth_percent']}% "
        f"annual growth under a {b['assumptions']['discount_rate_percent']}% discount rate. "
        f"The base case comes to ${b['base_case']['value_per_share']:.2f} per share."
    )
    wired(reply)

    result = await ve.explain_valuation("MSFT")

    assert result["guarded"] is False
    assert result["explanation"] == reply


async def test_no_credentials_means_no_model_call(monkeypatch, wired):
    provider = wired("unused")
    monkeypatch.setattr(ve, "has_llm_credentials", lambda _s: False)

    result = await ve.explain_valuation("MSFT")

    assert result["available"] is False
    assert provider.calls == 0


async def test_a_valuation_error_is_passed_through_not_explained(monkeypatch, wired):
    provider = wired("unused")

    async def failing(symbol, **_overrides):
        return {"symbol": symbol, "error": "free cash flow is negative"}

    monkeypatch.setattr(ve.valuation, "get_valuation", failing)

    result = await ve.explain_valuation("MSFT")

    assert result == {"symbol": "MSFT", "available": False,
                      "reason": "free cash flow is negative"}
    assert provider.calls == 0


def test_the_explanation_is_keyed_on_the_same_overrides_as_the_valuation():
    a = ve.valuation.override_key("msft", 0.1, None, 0.09, 10, "ttm")
    b = ve.valuation.override_key("MSFT", 0.1004, None, 0.0901, 10, "ttm")
    c = ve.valuation.override_key("MSFT", 0.12, None, 0.09, 10, "ttm")

    assert a == b  # quantised
    assert a != c
