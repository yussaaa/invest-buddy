"""Synthesiser node — merges all AgentResults into a final FinalReport.

Uses the smart model with chain-of-thought prompting to:
- Coherently integrate up to 5 diverse agent outputs
- Weigh contradictions (e.g. bullish fundamentals vs bearish technicals)
- Produce a confidence score and key risks/positives list
- Append the mandatory disclaimer
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

import structlog

from app.agents.base.schemas import AgentResult, Citation, FinalReport, UserPreferences
from app.config import get_settings
from app.models.factory import get_provider

log = structlog.get_logger(__name__)

SYNTHESISER_SYSTEM_PROMPT = """You are a senior investment research director synthesising analysis from multiple specialist agents.

You will receive reports from up to 5 specialist agents:
- Market Research: business context, news, filings
- Sentiment Analysis: news sentiment, analyst ratings, options sentiment
- Fundamental Analysis: financial statements, valuation ratios
- Technical Analysis: price patterns, momentum indicators
- Risk Assessment: volatility, VaR, beta, drawdown

Your job:
1. Synthesise all reports into a coherent, balanced narrative
2. Explicitly acknowledge when agents contradict each other (e.g. "fundamentals are strong but technicals suggest near-term weakness")
3. Weight the agents appropriately for the user's investment horizon and risk tolerance
4. Assign an overall confidence score (0.0-1.0) based on data quality and consistency
5. List 3-5 key risks and 3-5 key positives

IMPORTANT RULES:
- NEVER make explicit buy/sell/hold recommendations
- NEVER state price targets as facts ("will reach $X")
- Always use hedged language ("suggests", "indicates", "may", "historically associated with")
- Every major claim must reference which agent provided it

Respond with valid JSON:
{
  "summary": "2-3 sentence executive summary",
  "detailed_analysis": "Full multi-paragraph analysis with section headers using ##",
  "overall_confidence": 0.75,
  "key_risks": ["risk1", "risk2", "risk3"],
  "key_positives": ["positive1", "positive2"],
  "data_as_of": "YYYY-MM-DD"
}"""


def _format_agent_result(result: AgentResult) -> str:
    """Format a single AgentResult for the synthesiser context."""
    lines = [f"=== {result.agent_name.upper()} ==="]
    lines.append(f"Confidence: {result.confidence:.0%}")
    if result.caveats:
        lines.append(f"Caveats: {', '.join(result.caveats)}")
    lines.append("")
    lines.append(result.findings)

    if result.key_data_points:
        lines.append("\nKey Data:")
        for dp in result.key_data_points[:8]:  # limit for context window
            val = f"{dp.value} {dp.unit or ''}".strip()
            lines.append(f"  - {dp.label}: {val}")

    return "\n".join(lines)


async def synthesise_results(
    agent_results: list[AgentResult],
    ticker: str,
    query: str,
    user_preferences: Optional[UserPreferences] = None,
) -> FinalReport:
    """Call the smart model to synthesise all agent results."""
    settings = get_settings()
    provider = get_provider()

    if not agent_results:
        return FinalReport(
            summary="No analysis data available.",
            detailed_analysis="No specialist agents returned results.",
            overall_confidence=0.0,
            key_risks=["No data available for analysis"],
            key_positives=[],
        )

    # Build context from all agent results
    agent_context = "\n\n".join(_format_agent_result(r) for r in agent_results)

    user_msg = (
        f"Ticker: {ticker}\n"
        f"User query: {query}\n"
    )
    if user_preferences:
        user_msg += (
            f"User risk tolerance: {user_preferences.risk_tolerance}\n"
            f"Investment horizon: {user_preferences.investment_horizon}\n"
        )
    user_msg += f"\n\nSpecialist Agent Reports:\n\n{agent_context}"

    try:
        resp = await provider.complete(
            messages=[
                {"role": "system", "content": SYNTHESISER_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            model=settings.smart_model,
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=2048,
        )
        data = json.loads(resp.content)

        # Collect all citations from all agent results
        all_citations: list[Citation] = []
        for r in agent_results:
            all_citations.extend(r.citations)

        return FinalReport(
            summary=data.get("summary", "Analysis complete."),
            detailed_analysis=data.get("detailed_analysis", ""),
            overall_confidence=max(0.0, min(1.0, float(data.get("overall_confidence", 0.5)))),
            key_risks=data.get("key_risks", []),
            key_positives=data.get("key_positives", []),
            data_as_of=datetime.now(),
            citations=all_citations[:20],  # top 20 citations
        )

    except Exception as e:
        log.error("synthesiser_error", ticker=ticker, error=str(e))
        # Graceful fallback: concatenate findings
        combined = "\n\n".join(
            f"## {r.agent_name.replace('_', ' ').title()}\n{r.findings}"
            for r in agent_results
        )
        return FinalReport(
            summary=f"Analysis of {ticker} complete (synthesis error: {str(e)[:100]}).",
            detailed_analysis=combined,
            overall_confidence=0.3,
            key_risks=["Synthesis error — please review individual agent findings above"],
            key_positives=[],
        )
