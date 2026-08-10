"""Options Strategy Agent.

Reads the option chain for a ticker: how volatility is priced against what the
stock has actually been doing, and which contracts survive a liquidity,
moneyness and probability filter across four structures.

Every number this agent reports is computed deterministically in
app/services/options_* before the model sees it — greeks from Black-Scholes,
probabilities under a risk-neutral lognormal assumption, yields from the credit
and the collateral. The model interprets; it never calculates. That division is
the reason the panel and the agent can never disagree about a strike.

Uses the fast model: this is structured-data interpretation, like the technical
agent, not multi-step reasoning.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from typing import Any

import structlog

from app.agents.base.schemas import (
    AgentResult,
    Citation,
    DataPoint,
    ToolCallRecord,
)
from app.agents.base.state import AgentState
from app.agents.options.prompts import SYSTEM_PROMPT
from app.config import get_settings
from app.models.factory import get_provider
from app.tools.registry import ToolRegistry

log = structlog.get_logger(__name__)

_AGENT_NAME = "options_strategy"


class OptionsStrategyAgent:
    """Specialist agent for options pricing and strategy screening."""

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
        """Serialise tool data to a truncated JSON string for the LLM context.

        The options tools trim their own output well under this ceiling, so the
        truncation should never fire here — but it stays because a silent
        mid-JSON cut is exactly the failure it guards against.
        """
        if data is None:
            return "null"
        try:
            raw = json.dumps(data, default=str)
            return raw[:max_chars] + ("…[truncated]" if len(raw) > max_chars else "")
        except (TypeError, ValueError):
            return str(data)[:max_chars]

    async def run(self, state: AgentState) -> AgentResult:
        """Run the options strategy agent.

        Args:
            state: Current AgentState containing ticker, query, and user preferences.

        Returns:
            Validated AgentResult with volatility context, screened contracts and citations.
        """
        ticker = state["ticker"]
        start_time = time.monotonic()
        tool_calls: list[ToolCallRecord] = []
        caveats: list[str] = []

        log.info("options_agent_start", ticker=ticker)

        # ── 1. Concurrent tool calls ───────────────────────────────────────────
        tasks = [
            self._call_tool("get_options_snapshot", {"ticker": ticker}),
            self._call_tool("screen_option_strategies", {"ticker": ticker, "strategy": "all"}),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        tool_data: dict[str, Any] = {}
        for res in results:
            if isinstance(res, Exception):
                caveats.append(f"Tool call raised an unexpected exception: {res}")
                log.error("options_tool_exception", error=str(res))
                continue
            t_name, t_data, t_record = res
            tool_calls.append(t_record)
            if t_record.success:
                tool_data[t_name] = t_data
            else:
                caveats.append(f"{t_name} failed: {t_record.error}")

        # A ticker with no listed options is an ordinary answer, not a failure,
        # and it is worth saying so rather than letting the model improvise
        # around two empty payloads.
        snapshot = tool_data.get("get_options_snapshot") or {}
        screened = tool_data.get("screen_option_strategies") or {}
        if snapshot.get("error") or screened.get("error"):
            reason = snapshot.get("error") or screened.get("error")
            log.info("options_agent_no_chain", ticker=ticker, reason=reason)
            return AgentResult(
                agent_name=_AGENT_NAME,
                findings=(
                    f"No screenable option chain is available for {ticker}: {reason}. "
                    "Either the ticker has no listed options, or the provider returned "
                    "no expiries within the screened horizons."
                ),
                confidence=0.0,
                caveats=caveats + [f"Option chain unavailable: {reason}"],
                tool_calls=tool_calls,
            )

        # ── 2. Build LLM context string ────────────────────────────────────────
        context_parts = [f"Ticker: {ticker}\n"]

        section_map = {
            "get_options_snapshot": (
                "Volatility Context — implied vs realized, per-expiry summary"
            ),
            "screen_option_strategies": (
                "Screened Candidates — ranked contracts per strategy, with computed "
                "greeks and model-implied probabilities"
            ),
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
                            f"Analyse the options market for {ticker}. Here is the data:"
                            f"\n\n{context_str}"
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
            log.error("options_llm_json_parse_error", ticker=ticker, error=str(exc))
            caveats.append(f"LLM response could not be parsed as JSON: {exc}")
            llm_data = {}
        except Exception as exc:
            log.error("options_llm_error", ticker=ticker, error=str(exc))
            return AgentResult(
                agent_name=_AGENT_NAME,
                findings=(
                    f"Options analysis for {ticker} could not be completed due to "
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

        # The two structural caveats are appended rather than left to the model.
        # They are true of every options screen regardless of what came back, and
        # the negative-skew one in particular is the thing a reader most needs
        # and is least likely to supply for themselves.
        all_caveats.extend(_STRUCTURAL_CAVEATS)

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

        citations: list[Citation] = []
        citation_config = {
            "get_options_snapshot": ("market_data", f"{ticker} — Option Chain Volatility Context"),
            "screen_option_strategies": ("calculation", f"{ticker} — Options Strategy Screen"),
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
            "options_agent_complete",
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
            data_freshness=datetime.now(UTC),
            citations=citations,
            caveats=all_caveats,
            tool_calls=tool_calls,
        )


_STRUCTURAL_CAVEATS = [
    "A high probability of profit is not a high expected return. These screens rank "
    "contracts that win often and lose big; a 90%-probability short put collects a "
    "small credit while carrying the full downside of 100 shares.",
    "Probabilities are risk-neutral model estimates describing what the option is "
    "priced for, not forecasts of the underlying.",
    "Black-Scholes is a European model. Single-name equity options are American, so "
    "early assignment is possible and unmodelled, particularly on dividend payers.",
]
