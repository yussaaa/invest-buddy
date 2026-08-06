"""Options tools for the specialist agent.

Thin wrappers over app/services/options.py — the fetching, caching and
screening all live there, and duplicating any of it here would give the agent
a second implementation that could disagree with the panel the user is looking
at.

The one thing these do add is **trimming**. Agents serialise every tool result
through a 2000-character truncation and are not told when it fires, so an
oversized payload does not error — it arrives as a JSON fragment that the model
reads as though it were complete. A full chain is far past that, so these
return the top few rows per strategy with the columns an agent can actually
reason about, and there is a unit test holding the encoded size under budget.
"""

from __future__ import annotations

import structlog

from app.services import options, options_screen

log = structlog.get_logger(__name__)

# Deliberately below the 2000-character truncation, with room for the labels
# the agent wraps around it.
AGENT_STRATEGY_LIMIT = 3


async def get_options_snapshot(ticker: str) -> dict:
    """Volatility context and per-expiry summary — no per-contract rows.

    This answers "are options expensive on this name, and what is the chain
    shaped like", which is the part of the picture the other agents cannot see.
    """
    try:
        chain = await options.get_options_chain(ticker, horizon="both")
    except Exception as e:
        log.error("options_snapshot_failed", ticker=ticker, error=str(e))
        return {"ticker": ticker.upper(), "error": str(e)}

    if chain.get("error"):
        return {"ticker": ticker.upper(), "error": chain["error"]}

    volatility = chain.get("volatility") or {}
    expiries = [
        {
            "expiry": e.get("expiry"),
            "dte": e.get("dte"),
            "atm_iv": _round(e.get("atm_iv")),
            "put_call_oi_ratio": _round(e.get("put_call_oi_ratio")),
        }
        for e in chain.get("expiries", [])
    ]

    return {
        "ticker": chain.get("symbol"),
        "spot": _round(chain.get("spot"), 2),
        "atm_iv_30d": _round(volatility.get("atm_iv_30d")),
        "realized_vol_20d": _round(volatility.get("hv_20")),
        "realized_vol_252d": _round(volatility.get("hv_252")),
        "iv_vs_realized_ratio": _round(volatility.get("iv_hv_ratio")),
        # Named exactly as the payload names it, so the model cannot mistake
        # the proxy for a real IV rank.
        "iv_rank_available": volatility.get("iv_rank_available", False),
        "iv_rank": _round(volatility.get("iv_rank"), 1),
        "iv_percentile_vs_realized": _round(volatility.get("iv_percentile_vs_realized"), 1),
        "volatility_method": volatility.get("method"),
        "expiries": expiries,
        "next_earnings": chain.get("next_earnings"),
        "warnings": chain.get("warnings", []),
    }


async def screen_option_strategies(
    ticker: str, strategy: str = "all", limit: int = AGENT_STRATEGY_LIMIT
) -> dict:
    """Top-ranked contracts per strategy, trimmed to fit an agent's context."""
    try:
        result = await options.get_option_strategies(ticker, strategy, limit=limit)
    except Exception as e:
        log.error("options_screen_failed", ticker=ticker, error=str(e))
        return {"ticker": ticker.upper(), "error": str(e)}

    if result.get("error"):
        return {"ticker": ticker.upper(), "error": result["error"]}

    candidates = result.get("candidates", [])
    by_strategy: dict[str, list[dict]] = {}
    for row in candidates:
        by_strategy.setdefault(row["strategy"], []).append(row)

    return {
        "ticker": result.get("symbol"),
        "spot": _round(result.get("spot"), 2),
        "probability_model": result.get("probability_model"),
        "universe_counts": result.get("universe_counts"),
        "strategies": {
            name: options_screen.compact_rows(rows, limit=limit)
            for name, rows in by_strategy.items()
        },
        "note": (
            "Probabilities are risk-neutral model estimates. A high probability of "
            "profit corresponds to a small credit against a large tail loss."
        ),
    }


def _round(value, digits: int = 4):
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None
