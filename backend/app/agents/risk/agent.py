"""Risk Assessment Agent.

Computes a comprehensive quantitative risk profile — historical volatility, VaR,
beta, Sharpe/Sortino ratios, and maximum drawdown — and synthesises them into a
structured risk assessment using the smart model.

Uses the smart model because risk synthesis requires careful reasoning about
interacting statistical properties and contextualisation against benchmarks.
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
from app.agents.risk.prompts import SYSTEM_PROMPT, TOOL_LIST
from app.config import get_settings
from app.models.factory import get_provider
from app.tools.registry import ToolRegistry

log = structlog.get_logger(__name__)

_AGENT_NAME = "risk_assessment"


class RiskAssessmentAgent:
    """Specialist agent for quantitative equity risk assessment.

    Uses the smart model (GPT-4o / Claude Sonnet) because risk synthesis requires
    careful multi-factor reasoning, benchmark comparison, and caveat identification.
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
        """Run the risk assessment agent.

        Args:
            state: Current AgentState containing ticker, query, and user preferences.

        Returns:
            Validated AgentResult with risk findings, quantitative metrics, and citations.
        """
        ticker = state["ticker"]
        start_time = time.monotonic()
        tool_calls: list[ToolCallRecord] = []
        caveats: list[str] = []

        log.info("risk_agent_start", ticker=ticker)

        # ── 1. Concurrent tool calls ───────────────────────────────────────────
        tasks = [
            # Two volatility windows: short-term (30d) and long-term (252d)
            self._call_tool("compute_historical_volatility", {"ticker": ticker, "window": 30}),
            self._call_tool("compute_historical_volatility", {"ticker": ticker, "window": 252}),
            self._call_tool("compute_var", {"ticker": ticker, "confidence": 0.95}),
            self._call_tool("compute_var", {"ticker": ticker, "confidence": 0.99}),
            self._call_tool("compute_beta", {"ticker": ticker, "benchmark": "SPY"}),
            self._call_tool("compute_sharpe_sortino", {"ticker": ticker}),
            self._call_tool("compute_max_drawdown", {"ticker": ticker}),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        tool_data: dict[str, Any] = {}
        for res in results:
            if isinstance(res, Exception):
                caveats.append(f"Tool call raised an unexpected exception: {res}")
                log.error("risk_tool_exception", error=str(res))
                continue
            t_name, t_data, t_record = res
            tool_calls.append(t_record)
            if t_record.success:
                # Disambiguate the two volatility and VaR calls by their arguments
                key = t_name
                if t_name == "compute_historical_volatility":
                    window = t_record.arguments.get("window", 30)
                    key = f"compute_historical_volatility_{window}d"
                elif t_name == "compute_var":
                    conf = t_record.arguments.get("confidence", 0.95)
                    key = f"compute_var_{int(conf * 100)}pct"
                tool_data[key] = t_data
            else:
                caveats.append(f"{t_name} (args={t_record.arguments}) failed: {t_record.error}")

        # ── 2. Build LLM context string ────────────────────────────────────────
        context_parts = [f"Ticker: {ticker}\n"]

        section_map = {
            "compute_historical_volatility_30d": "Historical Volatility (30-Day Window, Annualised)",
            "compute_historical_volatility_252d": "Historical Volatility (252-Day Window, Annualised)",
            "compute_var_95pct": "Value-at-Risk (VaR) at 95% Confidence",
            "compute_var_99pct": "Value-at-Risk (VaR) at 99% Confidence",
            "compute_beta": "Beta vs. SPY (1-Year Rolling)",
            "compute_sharpe_sortino": "Sharpe Ratio, Sortino Ratio, Calmar Ratio",
            "compute_max_drawdown": "Maximum Drawdown Analysis",
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
            log.error("risk_llm_json_parse_error", ticker=ticker, error=str(exc))
            caveats.append(f"LLM response could not be parsed as JSON: {exc}")
            llm_data = {}
        except Exception as exc:
            log.error("risk_llm_error", ticker=ticker, error=str(exc))
            return AgentResult(
                agent_name=_AGENT_NAME,
                findings=(
                    f"Risk assessment for {ticker} could not be completed due to "
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
            "compute_historical_volatility_30d": ("calculation", f"{ticker} — 30-Day Historical Volatility"),
            "compute_historical_volatility_252d": ("calculation", f"{ticker} — 252-Day Historical Volatility"),
            "compute_var_95pct": ("calculation", f"{ticker} — VaR (95% Confidence)"),
            "compute_var_99pct": ("calculation", f"{ticker} — VaR (99% Confidence)"),
            "compute_beta": ("calculation", f"{ticker} — Beta vs. SPY"),
            "compute_sharpe_sortino": ("calculation", f"{ticker} — Sharpe/Sortino/Calmar Ratios"),
            "compute_max_drawdown": ("calculation", f"{ticker} — Maximum Drawdown Analysis"),
        }
        for key, (source_type, title) in citation_config.items():
            if tool_data.get(key) is not None:
                citations.append(Citation(
                    source_type=source_type,  # type: ignore[arg-type]
                    title=title,
                    ticker=ticker,
                    excerpt=f"Quantitative risk metric computed via {key.rsplit('_', 1)[0]} tool",
                ))

        total_latency_ms = int((time.monotonic() - start_time) * 1000)
        log.info(
            "risk_agent_complete",
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
