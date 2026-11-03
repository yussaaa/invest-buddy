"""A plain-English read of the valuation panel — the most guarded prose in the app.

The sibling explainers (technicals, options) rely on their system prompt alone.
This one cannot. Its whole subject is the gap between a price and a number a
model produced, and the shortest sentence about that gap is a verdict —
"undervalued", "cheap", "fair value is $480". So the model's output is checked
twice before anyone sees it:

- `check_investment_advice(mode="valuation")`, which bans the verdict
  vocabulary in every wording, hedged or not;
- `check_numeric_grounding` against the same brief the model was given, so a
  figure that is not in the valuation cannot appear in its description.

A reply that fails either is not shown. In its place goes a templated
explanation built from the same brief — the mechanism `chat/graph.py`'s
`_hedged_reply` uses — flagged `guarded: true`. The reader always gets an
explanation; it is only sometimes the model's.

The brief is the one place numbers are put into prose scale: rates as percents,
cash flow in billions, per-share values in dollars. Giving the model ratios and
raw dollars and then grounding its "11.8%" against 0.118 would flag every
correct sentence it writes.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import structlog

from app.agents.base.guardrails import check_investment_advice, check_numeric_grounding
from app.config import get_settings
from app.models.factory import get_provider
from app.services import valuation
from app.services.cache import cached_async
from app.services.llm_guard import has_llm_credentials, missing_credentials_reason
from app.services.valuation_math import DEFAULT_YEARS

log = structlog.get_logger(__name__)

EXPLANATION_TTL = 900.0

EXPLAIN_SYSTEM_PROMPT = """You explain a discounted cash flow valuation to someone reading it on screen.

You will be given a brief: the price, the growth rate the price implies, the
model's base case with its assumptions, a bear and bull case, and the range of
a sensitivity grid. Every number is already in the units to quote it in.

Write 3 short paragraphs:
1. What the price implies — lead with the implied growth rate and the
   assumptions it is computed under. This is the question worth asking.
2. The base case — its assumptions, its value per share, how far that sits from
   the price, and how much of it is terminal value.
3. How fragile it is — the bear and bull cases and the sensitivity range, and
   which assumption moves the answer most.

Rules:
- Describe what the model computes. Never say what the stock *is*: do not use
  the words undervalued, overvalued, cheap, expensive, bargain, mispriced,
  fairly valued, fair value or intrinsic value, in any wording.
- Do not recommend buying, selling or holding, and do not predict prices.
- Quote only numbers that appear in the brief, as they appear there.
- Plain prose, no markdown headings or bullets. Under 200 words total."""

DISCLAIMER = (
    "A DCF is only as good as its assumptions, and these are estimates. This is "
    "not investment advice."
)


def _pct(value: Any, digits: int = 1) -> float | None:
    return round(value * 100, digits) if isinstance(value, (int, float)) else None


def _bn(value: Any) -> float | None:
    return round(value / 1e9, 1) if isinstance(value, (int, float)) else None


def _usd(value: Any) -> float | None:
    return round(value, 2) if isinstance(value, (int, float)) else None


def brief(v: dict) -> dict:
    """The valuation in prose scale — the model's input and its grounding evidence."""
    a = v.get("assumptions") or {}
    inputs = v.get("inputs") or {}
    base = v.get("base_case") or {}
    price = inputs.get("price")
    value = base.get("value_per_share")

    grid = [
        cell
        for row in (v.get("sensitivity") or {}).get("values") or []
        for cell in row
        if isinstance(cell, (int, float))
    ]

    return {
        "ticker": v.get("symbol"),
        "price": _usd(price),
        "implied_growth_percent": _pct(v.get("implied_growth")),
        "assumptions": {
            "initial_growth_percent": _pct(a.get("initial_growth")),
            "initial_growth_source": a.get("initial_growth_source"),
            "terminal_growth_percent": _pct(a.get("terminal_growth")),
            "discount_rate_percent": _pct(a.get("discount_rate")),
            "discount_rate_source": a.get("discount_rate_source"),
            "years": a.get("years"),
        },
        "fcf_base_billions": _bn(inputs.get("fcf_base_used")),
        "fcf_base": inputs.get("fcf_base"),
        "fcf_dispersion_percent": _pct(inputs.get("fcf_dispersion"), 0),
        "base_case": {
            "value_per_share": _usd(value),
            "vs_price_percent": (
                round((value - price) / price * 100, 1) if value and price else None
            ),
            "terminal_value_share_percent": _pct(base.get("terminal_value_share"), 0),
            "implied_exit_fcf_multiple": base.get("implied_exit_fcf_multiple"),
        },
        "scenarios": [
            {
                "case": s.get("case"),
                "value_per_share": _usd(s.get("value_per_share")),
                "initial_growth_percent": _pct((s.get("assumptions") or {}).get("initial_growth")),
                "terminal_growth_percent": _pct((s.get("assumptions") or {}).get("terminal_growth")),
                "discount_rate_percent": _pct((s.get("assumptions") or {}).get("discount_rate")),
            }
            for s in v.get("scenarios") or []
        ],
        "sensitivity_range": (
            {"low": _usd(min(grid)), "high": _usd(max(grid))} if grid else None
        ),
    }


def _money(value: float) -> str:
    return f"${value:,.2f}"


def templated_explanation(b: dict) -> str:
    """The fallback: the same three paragraphs, written by arithmetic.

    Built only from `b`, so it passes the grounding check by construction, and
    worded in the brief's own vocabulary, so it passes the advice check — both
    pinned in tests/unit/test_valuation_explain.py.
    """
    a = b.get("assumptions") or {}
    base = b.get("base_case") or {}
    paragraphs: list[str] = []

    terms = (
        f"a {a.get('discount_rate_percent')}% discount rate and "
        f"{a.get('terminal_growth_percent')}% terminal growth over {a.get('years')} years"
    )
    if b.get("price") is not None and b.get("implied_growth_percent") is not None:
        paragraphs.append(
            f"At {_money(b['price'])}, a DCF using {terms} needs about "
            f"{b['implied_growth_percent']}% annual free cash flow growth to reproduce "
            "the price. That growth rate is the market's assumption, stated in a form "
            "that can be compared with the company's history."
        )
    else:
        paragraphs.append(
            f"Under {terms}, no plausible growth rate reproduces the current price, "
            "which usually means the cash flow base is small relative to the market value."
        )

    if base.get("value_per_share") is not None:
        line = (
            f"The base case assumes {a.get('initial_growth_percent')}% initial growth and "
            f"comes to {_money(base['value_per_share'])} per share"
        )
        gap = base.get("vs_price_percent")
        if gap is not None:
            line += f", {abs(gap)}% {'above' if gap > 0 else 'below'} the price"
        line += "."
        share = base.get("terminal_value_share_percent")
        if share is not None:
            line += f" Terminal value is {share}% of that figure, " + (
                "so most of it rests on assumptions about the distant future."
                if share >= 50
                else "so the explicit forecast years carry most of the weight."
            )
        paragraphs.append(line)

    cases = {s.get("case"): s.get("value_per_share") for s in b.get("scenarios") or []}
    spread = b.get("sensitivity_range") or {}
    if cases.get("bear") is not None and cases.get("bull") is not None:
        line = (
            f"The bear and bull cases come to {_money(cases['bear'])} and "
            f"{_money(cases['bull'])}."
        )
        if spread.get("low") is not None and spread.get("high") is not None:
            line += (
                f" Across the sensitivity grid the value runs from {_money(spread['low'])} "
                f"to {_money(spread['high'])}: the discount rate and terminal growth move "
                "the result more than anything observed in the filings."
            )
        paragraphs.append(line)

    return "\n\n".join(paragraphs)


def review(text: str, b: dict) -> list[str]:
    """Why a model reply may not be shown. Empty means it may."""
    problems = [f"advice: {hit}" for hit in check_investment_advice(text, mode="valuation")]
    problems += [f"ungrounded: {n}" for n in check_numeric_grounding(text, b)]
    return problems


async def explain_valuation(
    symbol: str,
    *,
    growth: float | None = None,
    terminal_growth: float | None = None,
    discount_rate: float | None = None,
    years: int = DEFAULT_YEARS,
    fcf_base: str = "ttm",
) -> dict:
    """Describe the valuation the panel is showing, for exactly these overrides."""
    symbol = symbol.upper()
    settings = get_settings()

    if not has_llm_credentials(settings):
        return {
            "symbol": symbol,
            "available": False,
            "reason": missing_credentials_reason(settings),
        }

    overrides = dict(
        growth=growth,
        terminal_growth=terminal_growth,
        discount_rate=discount_rate,
        years=years,
        fcf_base=fcf_base,
    )
    result = await valuation.get_valuation(symbol, **overrides)
    if "error" in result:
        return {"symbol": symbol, "available": False, "reason": result["error"]}

    b = brief(result)

    async def fetch() -> dict:
        response = await get_provider().complete(
            messages=[
                {"role": "system", "content": EXPLAIN_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Valuation brief for {symbol}:\n\n{json.dumps(b, default=str)}",
                },
            ],
            model=settings.fast_model,
            temperature=0.2,
            max_tokens=600,
        )
        text = response.content.strip()
        problems = review(text, b)
        if problems:
            log.warning("valuation_explain_guarded", symbol=symbol, problems=problems)
            text = templated_explanation(b)

        return {
            "symbol": symbol,
            "available": True,
            "explanation": text,
            "guarded": bool(problems),
            "model": response.model,
            "disclaimer": DISCLAIMER,
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    key = "valuation_explain:" + valuation.override_key(symbol, **overrides)
    try:
        return await cached_async(
            key, EXPLANATION_TTL, fetch, should_cache=lambda r: bool(r.get("explanation"))
        )
    except Exception as e:
        log.error("valuation_explain_failed", symbol=symbol, error=str(e))
        return {"symbol": symbol, "available": False, "reason": str(e)}
