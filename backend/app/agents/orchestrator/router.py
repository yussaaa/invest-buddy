"""Classifier node — routes the query to the right agents.

Uses the fast model with structured output to determine:
- Which specialist agents are needed
- The primary ticker
- The analysis depth (quick/standard/deep)
"""

from __future__ import annotations

import json
import time

import structlog

from app.agents.base.schemas import QueryClassification, UserPreferences
from app.config import get_settings
from app.models.factory import get_provider

log = structlog.get_logger(__name__)

CLASSIFIER_SYSTEM_PROMPT = """You are an AI query router for a stock investment analysis system.

Given a user's query about stocks, your job is to determine:
1. Which stock tickers are mentioned or implied
2. Which analysis modules are needed
3. How deep the analysis should be

Available analysis modules:
- market_research: Company overview, recent news, SEC filings, business context
- sentiment: News sentiment, analyst ratings, options market sentiment
- fundamental: Financial statements, valuation ratios, DCF, earnings
- technical: Price charts, RSI, MACD, Bollinger Bands, moving averages
- risk: Volatility, VaR, beta, Sharpe ratio, drawdown analysis
- options: Option chain pricing — implied vs realized volatility, and contracts that
  pass a liquidity and probability screen for cash-secured puts, covered calls,
  LEAPS calls and credit spreads

Include `options` ONLY when the query actually concerns options: when it mentions
options, premium, puts, calls, strikes, expiries, implied volatility, covered calls,
the wheel, LEAPS, assignment, or asks about option strategies. Most equity queries
get nothing from it, and it is the slowest module — leave it out by default, even
for "full analysis" requests that say nothing about options.

Depth levels:
- quick: Only 1-2 agents, surface-level analysis (for simple factual queries)
- standard: 3-4 agents, comprehensive analysis (for "should I invest" type queries)
- deep: All equity agents, exhaustive analysis (for "full analysis" requests)

You MUST respond with valid JSON matching this schema:
{
  "tickers": ["AAPL"],
  "primary_ticker": "AAPL",
  "required_agents": ["market_research", "fundamental", "technical"],
  "analysis_depth": "standard",
  "user_intent": "User wants to understand Apple's fundamental health and recent performance"
}"""


async def classify_query(
    query: str,
    ticker_hint: str | None = None,
    user_preferences: UserPreferences | None = None,
) -> QueryClassification:
    """Run the classifier node — returns QueryClassification."""
    settings = get_settings()
    provider = get_provider()

    user_msg = f"Query: {query}"
    if ticker_hint:
        user_msg += f"\nTicker hint: {ticker_hint}"
    if user_preferences:
        user_msg += f"\nUser preferences: depth={user_preferences.analysis_depth}, risk={user_preferences.risk_tolerance}"

    t0 = time.monotonic()
    try:
        resp = await provider.complete(
            messages=[
                {"role": "system", "content": CLASSIFIER_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            model=settings.fast_model,
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=512,
        )
        data = json.loads(resp.content)

        classification = QueryClassification(
            tickers=data.get("tickers", [ticker_hint] if ticker_hint else []),
            primary_ticker=data.get("primary_ticker", ticker_hint or ""),
            required_agents=data.get("required_agents", ["market_research", "fundamental"]),
            analysis_depth=data.get("analysis_depth", "standard"),
            user_intent=data.get("user_intent", query),
        )

        # Override depth from user preferences if set
        if user_preferences and user_preferences.analysis_depth != "standard":
            classification.analysis_depth = user_preferences.analysis_depth

        log.info(
            "query_classified",
            ticker=classification.primary_ticker,
            agents=classification.required_agents,
            depth=classification.analysis_depth,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
        return classification

    except Exception as e:
        log.error("classification_error", error=str(e))
        # Fallback: run the equity agents at standard depth.
        #
        # `options` is deliberately absent. This path is already a degraded one
        # — the classifier failed, so we have no idea what was asked — and the
        # options agent is the slowest module against the most rate-limited
        # data source. Adding it here would tax every classifier failure with an
        # option-chain fetch for a query that likely never mentioned options.
        return QueryClassification(
            tickers=[ticker_hint] if ticker_hint else [],
            primary_ticker=ticker_hint or "",
            required_agents=["market_research", "sentiment", "fundamental", "technical", "risk"],
            analysis_depth="standard",
            user_intent=query,
        )
