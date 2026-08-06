"""Shared Pydantic schemas used by all agents.

Every agent returns an AgentResult — a validated, structured output that the
orchestrator synthesizer consumes. Using structured outputs (not free-form text)
makes results programmatically auditable and prevents prompt injection from
propagating through the pipeline.
"""

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ── Atomic building blocks ──────────────────────────────────────────────────


class Citation(BaseModel):
    """A traceable source reference attached to a specific claim."""

    source_type: Literal["sec_filing", "news", "market_data", "web", "rag", "calculation"]
    title: str
    url: Optional[str] = None
    published_date: Optional[datetime] = None
    ticker: Optional[str] = None
    excerpt: Optional[str] = None  # The specific passage cited


class DataPoint(BaseModel):
    """A single numeric or categorical data point with its source."""

    label: str                          # e.g. "P/E Ratio", "RSI(14)"
    value: Any                          # float, str, dict…
    unit: Optional[str] = None          # e.g. "x", "%", "USD"
    as_of: Optional[datetime] = None
    citation: Optional[Citation] = None


class ToolCallRecord(BaseModel):
    """Audit record for a single tool invocation by an agent."""

    tool_name: str
    arguments: dict[str, Any]
    result_summary: str                 # Short human-readable summary
    success: bool
    error: Optional[str] = None
    latency_ms: int = 0
    cache_hit: bool = False


class GuardrailFlag(BaseModel):
    """A flag raised by the guardrails node."""

    flag_type: Literal[
        "hallucination",
        "investment_advice",
        "missing_citation",
        "low_confidence",
        "data_unavailable",
    ]
    agent: str
    detail: str
    severity: Literal["warning", "error"] = "warning"


# ── Per-agent result ────────────────────────────────────────────────────────


class AgentResult(BaseModel):
    """The structured output every specialist agent must return.

    The synthesizer node consumes a list of these to produce the final report.
    The LLM is forced to produce this JSON via response_format / structured outputs.
    """

    agent_name: str
    findings: str = Field(
        description="Narrative analysis in 2-5 paragraphs. Must not contain "
                    "direct investment advice (buy/sell/hold recommendations)."
    )
    key_data_points: list[DataPoint] = Field(
        default_factory=list,
        description="The most important numeric facts underpinning the findings.",
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Agent's self-assessed confidence in its findings (0=no data, 1=high confidence).",
    )
    data_freshness: Optional[datetime] = Field(
        None,
        description="Timestamp of the oldest data point used. Lets the user see how stale the data is.",
    )
    citations: list[Citation] = Field(
        default_factory=list,
        description="Every factual claim must be backed by at least one citation.",
    )
    caveats: list[str] = Field(
        default_factory=list,
        description="Known limitations, missing data, or assumptions made.",
    )
    tool_calls: list[ToolCallRecord] = Field(
        default_factory=list,
        description="Audit trail of every tool the agent invoked.",
    )


# ── Query classification ────────────────────────────────────────────────────


class QueryClassification(BaseModel):
    """Output of the classifier node — which agents to invoke and how deep."""

    tickers: list[str] = Field(description="Stock tickers mentioned or implied in the query.")
    primary_ticker: str = Field(description="The main ticker to analyse.")
    required_agents: list[
        Literal[
            "market_research", "sentiment", "fundamental", "technical", "risk", "options",
        ]
    ] = Field(description="Which specialist agents are needed for this query.")
    analysis_depth: Literal["quick", "standard", "deep"] = "standard"
    user_intent: str = Field(description="One-sentence summary of what the user wants to know.")


# ── Final synthesised report ────────────────────────────────────────────────


class FinalReport(BaseModel):
    """The synthesiser node's output — the response shown to the user."""

    summary: str = Field(description="Executive summary in 2-3 sentences.")
    detailed_analysis: str = Field(description="Full multi-section analysis.")
    overall_confidence: float = Field(ge=0.0, le=1.0)
    key_risks: list[str] = Field(default_factory=list)
    key_positives: list[str] = Field(default_factory=list)
    data_as_of: Optional[datetime] = None
    disclaimer: str = (
        "This analysis is for informational purposes only and does not constitute "
        "financial advice. Always consult a qualified financial advisor before making "
        "investment decisions."
    )
    citations: list[Citation] = Field(default_factory=list)


# ── User memory ─────────────────────────────────────────────────────────────


class UserPreferences(BaseModel):
    """Long-term user preferences loaded from PostgreSQL at the start of every run."""

    user_id: str
    risk_tolerance: Literal["conservative", "moderate", "aggressive"] = "moderate"
    investment_horizon: Literal["short", "medium", "long"] = "medium"
    preferred_sectors: list[str] = Field(default_factory=list)
    analysis_depth: Literal["quick", "standard", "deep"] = "standard"
    preferred_metrics: list[str] = Field(default_factory=list)
    recent_tickers: list[str] = Field(default_factory=list)
