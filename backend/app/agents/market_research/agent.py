"""Market Research Agent.

Builds a comprehensive qualitative and quantitative picture of a company's
market position, competitive dynamics, management quality, recent developments,
and upcoming catalysts. Draws on company overview data, SEC filings, news, web
intelligence, and earnings calendar.

Uses the fast model because the task is primarily synthesis and structuring of
retrieved information, not deep quantitative reasoning.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any

import structlog

from app.agents.base.schemas import (
    AgentResult,
    Citation,
    DataPoint,
    ToolCallRecord,
)
from app.agents.base.state import AgentState
from app.agents.market_research.prompts import SYSTEM_PROMPT, TOOL_LIST
from app.config import get_settings
from app.models.factory import get_provider
from app.tools.registry import ToolRegistry

log = structlog.get_logger(__name__)

_AGENT_NAME = "market_research"


class MarketResearchAgent:
    """Specialist agent for company and market research.

    Uses the fast model (GPT-4o-mini / Claude Haiku) because the task is primarily
    information synthesis and structuring from retrieved data sources.
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._provider = get_provider()
        self._registry = ToolRegistry.get()

    async def _call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> tuple[str, Any, ToolCallRecord]:
        """Execute a single tool and return (tool_name, data, record)."""
        t0 = time.monotonic()
        result = await self._registry.execute(tool_name, arguments)
        latency_ms = int((time.monotonic() - t0) * 1000)

        record = ToolCallRecord(
            tool_name=tool_name,
            arguments=arguments,
            result_summary=(
                f"Success — {type(result.data).__name__} returned"
                if result.success
                else f"Failed — {result.error}"
            ),
            success=result.success,
            error=result.error,
            latency_ms=result.latency_ms or latency_ms,
            cache_hit=result.cache_hit,
        )
        return tool_name, result.data if result.success else None, record

    @staticmethod
    def _safe_json_str(data: Any, max_chars: int = 2500) -> str:
        """Serialise tool data to a truncated JSON string for the LLM context."""
        if data is None:
            return "null"
        try:
            raw = json.dumps(data, default=str)
            return raw[:max_chars] + ("…[truncated]" if len(raw) > max_chars else "")
        except (TypeError, ValueError):
            return str(data)[:max_chars]

    async def run(self, state: AgentState) -> AgentResult:
        """Run the market research agent.

        Args:
            state: Current AgentState containing ticker, query, and user preferences.

        Returns:
            Validated AgentResult with market research findings, data points, and citations.
        """
        ticker = state["ticker"]
        start_time = time.monotonic()
        tool_calls: list[ToolCallRecord] = []
        caveats: list[str] = []

        log.info("market_research_agent_start", ticker=ticker)

        # ── 1. Concurrent tool calls ───────────────────────────────────────────
        # Web search uses a structured query to target financial intelligence sources
        web_query = (
            f"{ticker} company strategy competitive landscape recent developments "
            f"site:reuters.com OR site:bloomberg.com OR site:wsj.com OR site:ft.com"
        )

        tasks = [
            self._call_tool("get_company_overview", {"ticker": ticker}),
            self._call_tool("get_recent_news", {"ticker": ticker, "days": 7, "max_results": 10}),
            self._call_tool("get_sec_filings", {"ticker": ticker, "filing_type": "10-K", "limit": 1}),
            self._call_tool("get_sec_filings", {"ticker": ticker, "filing_type": "10-Q", "limit": 2}),
            self._call_tool("get_sec_filings", {"ticker": ticker, "filing_type": "8-K", "limit": 3}),
            self._call_tool("search_web", {"query": web_query, "max_results": 5}),
            self._call_tool("get_earnings_calendar", {"ticker": ticker}),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        tool_data: dict[str, Any] = {}
        for res in results:
            if isinstance(res, Exception):
                caveats.append(f"Tool call raised an unexpected exception: {res}")
                log.error("market_research_tool_exception", error=str(res))
                continue
            t_name, t_data, t_record = res
            tool_calls.append(t_record)
            if t_record.success:
                # Disambiguate the three SEC filing calls by type
                key = t_name
                if t_name == "get_sec_filings":
                    filing_type = t_record.arguments.get("filing_type", "unknown")
                    key = f"get_sec_filings_{filing_type.replace('-', '_')}"
                tool_data[key] = t_data
            else:
                caveats.append(f"{t_name} (args={t_record.arguments}) failed: {t_record.error}")

        # ── 2. Build LLM context string ────────────────────────────────────────
        context_parts = [f"Ticker: {ticker}\n"]

        section_map = {
            "get_company_overview": "Company Overview (Profile, Sector, Market Cap, Description)",
            "get_recent_news": "Recent News (last 7 days)",
            "get_sec_filings_10_K": "SEC Filings — Most Recent 10-K (Annual Report)",
            "get_sec_filings_10_Q": "SEC Filings — Recent 10-Q (Quarterly Reports)",
            "get_sec_filings_8_K": "SEC Filings — Recent 8-K (Material Events)",
            "search_web": "Web Search Intelligence (Strategy, Competitive Landscape)",
            "get_earnings_calendar": "Earnings Calendar (Upcoming / Historical)",
        }
        for key, label in section_map.items():
            data = tool_data.get(key)
            context_parts.append(f"### {label}\n{self._safe_json_str(data)}")

        context_str = "\n\n".join(context_parts)

        # ── 3. LLM call ────────────────────────────────────────────────────────
        model = self._settings.fast_model
        try:
            response = await self._provider.complete(
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Analyse {ticker}. Here is the data:\n\n{context_str}"
                        ),
                    },
                ],
                model=model,
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=2048,
            )
            llm_data = json.loads(response.content)
        except json.JSONDecodeError as exc:
            log.error("market_research_llm_json_parse_error", ticker=ticker, error=str(exc))
            caveats.append(f"LLM response could not be parsed as JSON: {exc}")
            llm_data = {}
        except Exception as exc:
            log.error("market_research_llm_error", ticker=ticker, error=str(exc))
            return AgentResult(
                agent_name=_AGENT_NAME,
                findings=(
                    f"Market research for {ticker} could not be completed due to "
                    f"an LLM error: {exc}. Partial tool data was collected."
                ),
                confidence=0.0,
                caveats=caveats + [f"LLM call failed: {exc}"],
                tool_calls=tool_calls,
            )

        # ── 4. Parse LLM output into AgentResult fields ────────────────────────
        findings: str = llm_data.get("findings", "No findings generated.")
        raw_data_points: list[dict] = llm_data.get("key_data_points", [])
        confidence: float = float(llm_data.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))
        llm_caveats: list[str] = llm_data.get("caveats", [])
        all_caveats = caveats + (llm_caveats if isinstance(llm_caveats, list) else [])

        # Build DataPoint objects
        data_points: list[DataPoint] = []
        for dp in raw_data_points:
            if not isinstance(dp, dict):
                continue
            try:
                data_points.append(DataPoint(
                    label=str(dp.get("label", "Unknown")),
                    value=dp.get("value"),
                    unit=dp.get("unit"),
                ))
            except Exception:
                pass

        # Build citations — SEC filings and news get source-specific citation types
        citations: list[Citation] = []

        # Company overview
        if tool_data.get("get_company_overview") is not None:
            overview = tool_data["get_company_overview"]
            sector = ""
            if isinstance(overview, dict):
                sector = overview.get("sector", "") or overview.get("industry", "")
            citations.append(Citation(
                source_type="market_data",
                title=f"{ticker} — Company Overview{f' ({sector})' if sector else ''}",
                ticker=ticker,
                excerpt="Company profile, sector, market cap retrieved via get_company_overview tool",
            ))

        # SEC filings
        for filing_type in ("10_K", "10_Q", "8_K"):
            key = f"get_sec_filings_{filing_type}"
            if tool_data.get(key) is not None:
                display_type = filing_type.replace("_", "-")
                citations.append(Citation(
                    source_type="sec_filing",
                    title=f"{ticker} — SEC {display_type} Filing",
                    ticker=ticker,
                    excerpt=f"Retrieved via get_sec_filings (filing_type={display_type})",
                ))

        # News
        if tool_data.get("get_recent_news") is not None:
            citations.append(Citation(
                source_type="news",
                title=f"{ticker} — Recent News Articles (7-day)",
                ticker=ticker,
                excerpt="News headlines and summaries retrieved via get_recent_news tool",
            ))

        # Web search
        if tool_data.get("search_web") is not None:
            citations.append(Citation(
                source_type="web",
                title=f"{ticker} — Web Intelligence (Strategy & Competitive Landscape)",
                ticker=ticker,
                excerpt="Web search results retrieved via search_web tool",
            ))

        # Earnings calendar
        if tool_data.get("get_earnings_calendar") is not None:
            citations.append(Citation(
                source_type="market_data",
                title=f"{ticker} — Earnings Calendar",
                ticker=ticker,
                excerpt="Earnings dates and EPS estimates retrieved via get_earnings_calendar tool",
            ))

        # Determine data freshness from company overview or default to now
        data_freshness: datetime | None = None
        overview = tool_data.get("get_company_overview")
        if isinstance(overview, dict):
            date_str = overview.get("last_updated") or overview.get("fiscal_year_end")
            if date_str:
                try:
                    data_freshness = datetime.fromisoformat(str(date_str))
                except (ValueError, TypeError):
                    pass
        if data_freshness is None:
            data_freshness = datetime.now(timezone.utc)

        total_latency_ms = int((time.monotonic() - start_time) * 1000)
        log.info(
            "market_research_agent_complete",
            ticker=ticker,
            confidence=confidence,
            data_points=len(data_points),
            latency_ms=total_latency_ms,
        )

        return AgentResult(
            agent_name=_AGENT_NAME,
            findings=findings,
            key_data_points=data_points,
            confidence=confidence,
            data_freshness=data_freshness,
            citations=citations,
            caveats=all_caveats,
            tool_calls=tool_calls,
        )
