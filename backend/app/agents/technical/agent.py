"""Technical Analysis Agent.

Computes and interprets price-based technical indicators (RSI, MACD, Bollinger Bands,
moving averages, support/resistance) to produce a structured technical assessment.

Uses the fast model because technical analysis involves interpretation of structured
numerical data — pattern recognition rather than deep multi-step reasoning.
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
from app.agents.technical.prompts import SYSTEM_PROMPT, TOOL_LIST
from app.config import get_settings
from app.models.factory import get_provider
from app.tools.registry import ToolRegistry

log = structlog.get_logger(__name__)

_AGENT_NAME = "technical_analysis"


class TechnicalAnalysisAgent:
    """Specialist agent for technical (price action) analysis.

    Uses the fast model (GPT-4o-mini / Claude Haiku) because the analysis is
    primarily structured-data interpretation, not deep multi-step reasoning.
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
    def _safe_json_str(data: Any, max_chars: int = 2000) -> str:
        """Serialise tool data to a truncated JSON string for the LLM context."""
        if data is None:
            return "null"
        try:
            raw = json.dumps(data, default=str)
            return raw[:max_chars] + ("…[truncated]" if len(raw) > max_chars else "")
        except (TypeError, ValueError):
            return str(data)[:max_chars]

    async def run(self, state: AgentState) -> AgentResult:
        """Run the technical analysis agent.

        Args:
            state: Current AgentState containing ticker, query, and user preferences.

        Returns:
            Validated AgentResult with technical findings, indicator values, and citations.
        """
        ticker = state["ticker"]
        start_time = time.monotonic()
        tool_calls: list[ToolCallRecord] = []
        caveats: list[str] = []

        log.info("technical_agent_start", ticker=ticker)

        # ── 1. Concurrent tool calls ───────────────────────────────────────────
        tasks = [
            self._call_tool("get_price_history", {"ticker": ticker, "period": "1y", "interval": "1d"}),
            self._call_tool("compute_rsi", {"ticker": ticker, "period": 14}),
            self._call_tool("compute_macd", {"ticker": ticker}),
            self._call_tool("compute_bollinger_bands", {"ticker": ticker, "window": 20}),
            self._call_tool("compute_moving_averages", {"ticker": ticker}),
            self._call_tool("compute_support_resistance", {"ticker": ticker}),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        tool_data: dict[str, Any] = {}
        for res in results:
            if isinstance(res, Exception):
                caveats.append(f"Tool call raised an unexpected exception: {res}")
                log.error("technical_tool_exception", error=str(res))
                continue
            t_name, t_data, t_record = res
            tool_calls.append(t_record)
            if t_record.success:
                tool_data[t_name] = t_data
            else:
                caveats.append(f"{t_name} failed: {t_record.error}")

        # ── 2. Build LLM context string ────────────────────────────────────────
        context_parts = [f"Ticker: {ticker}\n"]

        section_map = {
            "get_price_history": "Price History (1 Year, Daily OHLCV)",
            "compute_rsi": "RSI(14) — Relative Strength Index",
            "compute_macd": "MACD — Line, Signal, Histogram",
            "compute_bollinger_bands": "Bollinger Bands (20-day, 2σ) and %B",
            "compute_moving_averages": "Moving Averages (SMA & EMA: 20/50/200-day)",
            "compute_support_resistance": "Support and Resistance Levels",
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
            log.error("technical_llm_json_parse_error", ticker=ticker, error=str(exc))
            caveats.append(f"LLM response could not be parsed as JSON: {exc}")
            llm_data = {}
        except Exception as exc:
            log.error("technical_llm_error", ticker=ticker, error=str(exc))
            return AgentResult(
                agent_name=_AGENT_NAME,
                findings=(
                    f"Technical analysis for {ticker} could not be completed due to "
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

        # Build citations from successful tool calls
        citations: list[Citation] = []
        citation_config = {
            "get_price_history": ("market_data", f"{ticker} — Price History (1Y Daily)"),
            "compute_rsi": ("calculation", f"{ticker} — RSI(14) Calculation"),
            "compute_macd": ("calculation", f"{ticker} — MACD Calculation"),
            "compute_bollinger_bands": ("calculation", f"{ticker} — Bollinger Bands Calculation"),
            "compute_moving_averages": ("calculation", f"{ticker} — Moving Averages Calculation"),
            "compute_support_resistance": ("calculation", f"{ticker} — Support/Resistance Analysis"),
        }
        for key, (source_type, title) in citation_config.items():
            if tool_data.get(key) is not None:
                citations.append(Citation(
                    source_type=source_type,  # type: ignore[arg-type]
                    title=title,
                    ticker=ticker,
                    excerpt=f"Data retrieved via {key} tool",
                ))

        total_latency_ms = int((time.monotonic() - start_time) * 1000)
        log.info(
            "technical_agent_complete",
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
            data_freshness=datetime.now(timezone.utc),
            citations=citations,
            caveats=all_caveats,
            tool_calls=tool_calls,
        )
