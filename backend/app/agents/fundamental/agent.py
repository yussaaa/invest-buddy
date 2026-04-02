"""Fundamental Analysis Agent.

Calls financial data tools, constructs a structured context, and uses the smart
LLM to generate a CFA-quality fundamental analysis. Returns a validated AgentResult.

Tool calls run concurrently (asyncio.gather) to minimise wall-clock latency.
Every tool failure is caught individually and recorded as a caveat — the agent
degrades gracefully rather than failing entirely.
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
from app.agents.fundamental.prompts import SYSTEM_PROMPT, TOOL_LIST
from app.config import get_settings
from app.models.factory import get_provider
from app.tools.registry import ToolRegistry

log = structlog.get_logger(__name__)

_AGENT_NAME = "fundamental_analysis"


class FundamentalAnalysisAgent:
    """Specialist agent for company fundamental analysis.

    Uses the smart model (GPT-4o / Claude Sonnet) because fundamental analysis
    requires multi-step reasoning across several financial statements.
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
    def _safe_json_str(data: Any, max_chars: int = 3000) -> str:
        """Serialise tool data to a truncated JSON string for the LLM context."""
        if data is None:
            return "null"
        try:
            raw = json.dumps(data, default=str)
            return raw[:max_chars] + ("…[truncated]" if len(raw) > max_chars else "")
        except (TypeError, ValueError):
            return str(data)[:max_chars]

    async def run(self, state: AgentState) -> AgentResult:
        """Run the fundamental analysis agent.

        Args:
            state: Current AgentState containing ticker, query, and user preferences.

        Returns:
            Validated AgentResult with findings, data points, confidence, and citations.
        """
        ticker = state["ticker"]
        start_time = time.monotonic()
        tool_calls: list[ToolCallRecord] = []
        caveats: list[str] = []

        log.info("fundamental_agent_start", ticker=ticker)

        # ── 1. Concurrent tool calls ───────────────────────────────────────────
        tasks = [
            self._call_tool("get_income_statement", {"ticker": ticker, "quarterly": False}),
            self._call_tool("get_income_statement", {"ticker": ticker, "quarterly": True}),
            self._call_tool("get_balance_sheet", {"ticker": ticker, "quarterly": False}),
            self._call_tool("get_cash_flow", {"ticker": ticker, "quarterly": False}),
            self._call_tool("compute_financial_ratios", {"ticker": ticker}),
            self._call_tool("get_earnings_calendar", {"ticker": ticker}),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        tool_data: dict[str, Any] = {}
        for res in results:
            if isinstance(res, Exception):
                caveats.append(f"Tool call raised an unexpected exception: {res}")
                log.error("fundamental_tool_exception", error=str(res))
                continue
            t_name, t_data, t_record = res
            tool_calls.append(t_record)
            if t_record.success:
                # Disambiguate the two income_statement calls by quarterly flag
                key = t_name
                if t_name == "get_income_statement":
                    key = (
                        "get_income_statement_quarterly"
                        if t_record.arguments.get("quarterly")
                        else "get_income_statement_annual"
                    )
                tool_data[key] = t_data
            else:
                caveats.append(f"{t_name} failed: {t_record.error}")

        # ── 2. Build LLM context string ────────────────────────────────────────
        context_parts = [f"Ticker: {ticker}\n"]

        section_map = {
            "get_income_statement_annual": "Annual Income Statement",
            "get_income_statement_quarterly": "Quarterly Income Statement (last 4Q)",
            "get_balance_sheet": "Balance Sheet (Annual)",
            "get_cash_flow": "Cash Flow Statement (Annual)",
            "compute_financial_ratios": "Key Financial Ratios",
            "get_earnings_calendar": "Earnings Calendar",
        }
        for key, label in section_map.items():
            data = tool_data.get(key)
            context_parts.append(f"### {label}\n{self._safe_json_str(data)}")

        context_str = "\n\n".join(context_parts)

        # ── 3. LLM call ────────────────────────────────────────────────────────
        model = self._settings.smart_model
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
            log.error("fundamental_llm_json_parse_error", ticker=ticker, error=str(exc))
            caveats.append(f"LLM response could not be parsed as JSON: {exc}")
            llm_data = {}
        except Exception as exc:
            log.error("fundamental_llm_error", ticker=ticker, error=str(exc))
            return AgentResult(
                agent_name=_AGENT_NAME,
                findings=(
                    f"Fundamental analysis for {ticker} could not be completed due to "
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

        # Clamp to valid range and merge caveats
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
                pass  # Skip malformed data points silently

        # Build citations from successful tool calls
        citations: list[Citation] = []
        citation_source_map = {
            "get_income_statement_annual": "Income Statement (Annual)",
            "get_income_statement_quarterly": "Income Statement (Quarterly)",
            "get_balance_sheet": "Balance Sheet",
            "get_cash_flow": "Cash Flow Statement",
            "compute_financial_ratios": "Computed Financial Ratios",
            "get_earnings_calendar": "Earnings Calendar",
        }
        for key, label in citation_source_map.items():
            if tool_data.get(key) is not None:
                citations.append(Citation(
                    source_type="market_data",
                    title=f"{ticker} — {label}",
                    ticker=ticker,
                    excerpt=f"Data retrieved via {key} tool",
                ))

        # Determine data freshness from earnings calendar or fallback to now
        data_freshness: datetime | None = None
        ec = tool_data.get("get_earnings_calendar")
        if ec and isinstance(ec, dict):
            date_str = ec.get("last_earnings_date") or ec.get("next_earnings_date")
            if date_str:
                try:
                    data_freshness = datetime.fromisoformat(str(date_str))
                except (ValueError, TypeError):
                    pass
        if data_freshness is None:
            data_freshness = datetime.now(timezone.utc)

        total_latency_ms = int((time.monotonic() - start_time) * 1000)
        log.info(
            "fundamental_agent_complete",
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
