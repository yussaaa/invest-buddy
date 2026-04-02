"""LangGraph AgentState — the single source of truth for the entire pipeline.

Every node in the graph reads from and writes to this TypedDict.
LangGraph checkpoints this state between nodes, enabling streaming, retries,
and inspection of partial results.
"""

from typing import Annotated, Any, Optional
from typing_extensions import TypedDict
import operator

from app.agents.base.schemas import (
    AgentResult,
    Citation,
    GuardrailFlag,
    QueryClassification,
    UserPreferences,
    FinalReport,
)


def _merge_dicts(a: dict, b: dict) -> dict:
    """Custom reducer: merge dicts (used for latency_breakdown)."""
    return {**a, **b}


def _append_list(a: list, b: list) -> list:
    """Custom reducer: concatenate lists (used for tool_calls_made)."""
    return a + b


class AgentState(TypedDict):
    """Complete state flowing through the LangGraph pipeline.

    Fields annotated with Annotated[X, operator.add] use LangGraph's
    reducer pattern — parallel nodes can safely append to these lists
    without overwriting each other.
    """

    # ── Input ────────────────────────────────────────────────────────────
    query: str
    ticker: str                             # Primary ticker symbol (e.g. "AAPL")
    user_id: str
    session_id: str
    user_preferences: UserPreferences

    # ── Routing (set by classifier node) ─────────────────────────────────
    classification: Optional[QueryClassification]
    required_agents: list[str]
    analysis_depth: str                     # "quick" | "standard" | "deep"

    # ── RAG context (set by prefetch node) ───────────────────────────────
    retrieved_documents: list[dict]         # Qdrant payloads + relevance scores
    entity_graph: dict[str, Any]            # Extracted entity relationships

    # ── Per-agent results (populated in parallel) ─────────────────────────
    market_research_result: Optional[AgentResult]
    sentiment_result: Optional[AgentResult]
    fundamental_result: Optional[AgentResult]
    technical_result: Optional[AgentResult]
    risk_result: Optional[AgentResult]

    # ── Guardrails (set by guardrails node) ──────────────────────────────
    guardrail_flags: Annotated[list[GuardrailFlag], _append_list]
    hallucination_score: float              # 0.0 (clean) → 1.0 (hallucinated)
    all_citations: Annotated[list[Citation], _append_list]

    # ── Final output (set by synthesiser node) ────────────────────────────
    final_report: Optional[FinalReport]
    disclaimer_appended: bool

    # ── Observability (accumulated across all nodes) ──────────────────────
    tool_calls_made: Annotated[list[dict], _append_list]
    latency_breakdown: Annotated[dict[str, float], _merge_dicts]
    run_id: str                             # Unique ID for this analysis run
    error: Optional[str]                    # Set if any node fails gracefully
