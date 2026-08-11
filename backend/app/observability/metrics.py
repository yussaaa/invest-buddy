"""Prometheus custom metrics for the Agent Invest platform.

Metrics are emitted from:
  - persistence_node (after each analysis run)
  - ToolRegistry.execute() (each tool call)
  - guardrails_node (each flag raised)

Scraped by Prometheus at /metrics (via prometheus_client ASGI app in main.py).
Visualized in Grafana dashboards.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, Summary

# ── Analysis Run Metrics ──────────────────────────────────────────────────────

ANALYSIS_RUNS_TOTAL = Counter(
    "analysis_runs_total",
    "Total number of analysis runs",
    ["status", "depth", "ticker"],
)

ANALYSIS_LATENCY_SECONDS = Histogram(
    "analysis_latency_seconds",
    "End-to-end analysis latency in seconds",
    ["depth"],
    buckets=[5, 10, 15, 20, 30, 45, 60, 90, 120],
)

AGENT_LATENCY_SECONDS = Histogram(
    "agent_latency_seconds",
    "Per-agent latency in seconds",
    ["agent"],
    buckets=[1, 2, 3, 5, 8, 10, 15, 20, 30],
)

# ── RAGAS Quality Metrics ─────────────────────────────────────────────────────

RAGAS_FAITHFULNESS = Gauge(
    "ragas_faithfulness",
    "Latest RAGAS faithfulness score (0-1)",
)

RAGAS_ANSWER_RELEVANCY = Gauge(
    "ragas_answer_relevancy",
    "Latest RAGAS answer relevancy score (0-1)",
)

RAGAS_CONTEXT_PRECISION = Gauge(
    "ragas_context_precision",
    "Latest RAGAS context precision score (0-1)",
)

OVERALL_CONFIDENCE = Gauge(
    "overall_confidence",
    "Latest synthesis overall confidence score (0-1)",
)

HALLUCINATION_SCORE = Gauge(
    "hallucination_score",
    "Latest max hallucination score across agents (0-1, lower is better)",
)

# ── Guardrail Metrics ─────────────────────────────────────────────────────────

GUARDRAIL_TRIGGERS_TOTAL = Counter(
    "guardrail_triggers_total",
    "Total guardrail flags raised",
    ["flag_type", "severity"],
)

# ── Tool Call Metrics ─────────────────────────────────────────────────────────

TOOL_CALLS_TOTAL = Counter(
    "tool_calls_total",
    "Total tool invocations",
    ["tool_name", "cache_hit", "success"],
)

TOOL_LATENCY_SECONDS = Histogram(
    "tool_latency_seconds",
    "Tool call latency in seconds",
    ["tool_name"],
    buckets=[0.1, 0.25, 0.5, 1, 2, 3, 5, 10],
)

# ── Market Data Cache Metrics ─────────────────────────────────────────────────

# `key_kind` is deliberately the key PREFIX (quote, hist, breadth…), never the
# symbol — per-symbol labels would be thousands of series for no insight.
MARKET_CACHE_REQUESTS_TOTAL = Counter(
    "market_cache_requests_total",
    "Market data cache lookups by tier served",
    ["key_kind", "tier"],  # l1 | l2 | miss | stale | error
)

MARKET_FETCH_SECONDS = Histogram(
    "market_fetch_seconds",
    "Upstream market data fetch latency in seconds (cache misses only)",
    ["key_kind"],
    buckets=[0.25, 0.5, 1, 2, 5, 10, 20, 40],
)

# ── Chat Agent Metrics ────────────────────────────────────────────────────────

CHAT_TURNS_TOTAL = Counter(
    "chat_turns_total",
    "Chat turns completed",
    ["blocked"],  # "true" when the drafted reply was replaced
)

# Sustained above roughly 2% of turns means the system prompt is losing, not
# that the guardrail is working — the prompt is what should keep replies clean.
CHAT_GUARDRAIL_BLOCKS_TOTAL = Counter(
    "chat_guardrail_blocks_total",
    "Chat replies replaced by the guardrail",
    ["reason"],  # investment_advice | ungrounded_figure
)

CHAT_SIGNALS_EMITTED = Histogram(
    "chat_signals_emitted",
    "Signals in the set a chat turn was grounded in",
    buckets=[0, 1, 2, 3, 5, 8, 13],
)

# ── Token / Cost Metrics ──────────────────────────────────────────────────────

TOKEN_USAGE_TOTAL = Counter(
    "token_usage_total",
    "Total LLM tokens consumed",
    ["model", "type"],  # type: prompt | completion
)

ESTIMATED_COST_USD = Counter(
    "estimated_cost_usd",
    "Estimated LLM API cost in USD",
    ["model"],
)

# Approximate pricing per 1M tokens (input/output) — update as prices change
_MODEL_PRICING = {
    # OpenAI
    "gpt-4o":        {"prompt": 2.50, "completion": 10.00},
    "gpt-4o-mini":   {"prompt": 0.15, "completion": 0.60},
    # Anthropic
    "claude-sonnet-4-6":  {"prompt": 3.00, "completion": 15.00},
    "claude-haiku-4-5":    {"prompt": 0.80, "completion": 4.00},
    # Local (free)
    "qwen2.5:7b":    {"prompt": 0.00, "completion": 0.00},
    "qwen2.5:14b":   {"prompt": 0.00, "completion": 0.00},
}


def record_token_cost(model: str, prompt_tokens: int, completion_tokens: int) -> None:
    """Estimate and record cost for an LLM call."""
    pricing = _MODEL_PRICING.get(model, {"prompt": 1.0, "completion": 3.0})
    cost = (prompt_tokens * pricing["prompt"] + completion_tokens * pricing["completion"]) / 1_000_000
    if cost > 0:
        ESTIMATED_COST_USD.labels(model=model).inc(cost)

# ── Helper Functions ──────────────────────────────────────────────────────────


def record_analysis_metrics(state: dict) -> None:
    """Record all metrics from a completed analysis run.

    Called from persistence_node after the pipeline finishes.
    """
    status = "completed" if state.get("final_report") else "error"
    depth = state.get("analysis_depth", "standard")
    ticker = state.get("ticker", "unknown")

    # Run counter
    ANALYSIS_RUNS_TOTAL.labels(status=status, depth=depth, ticker=ticker).inc()

    # Latency
    latency = state.get("latency_breakdown", {})
    total = latency.get("total")
    if total:
        ANALYSIS_LATENCY_SECONDS.labels(depth=depth).observe(total)

    for agent in ["market_research", "sentiment", "fundamental", "technical", "risk"]:
        agent_lat = latency.get(agent)
        if agent_lat:
            AGENT_LATENCY_SECONDS.labels(agent=agent).observe(agent_lat)

    # RAGAS scores
    eval_scores = state.get("eval_metadata", {})
    if eval_scores.get("faithfulness", -1) >= 0:
        RAGAS_FAITHFULNESS.set(eval_scores["faithfulness"])
    if eval_scores.get("answer_relevancy", -1) >= 0:
        RAGAS_ANSWER_RELEVANCY.set(eval_scores["answer_relevancy"])
    if eval_scores.get("context_precision", -1) >= 0:
        RAGAS_CONTEXT_PRECISION.set(eval_scores["context_precision"])

    # Confidence + hallucination
    report = state.get("final_report")
    if report and hasattr(report, "overall_confidence"):
        OVERALL_CONFIDENCE.set(report.overall_confidence)
    halluc = state.get("hallucination_score")
    if halluc is not None:
        HALLUCINATION_SCORE.set(halluc)

    # Guardrail flags
    for flag in state.get("guardrail_flags", []):
        flag_type = flag.flag_type if hasattr(flag, "flag_type") else flag.get("flag_type", "unknown")
        severity = flag.severity if hasattr(flag, "severity") else flag.get("severity", "warning")
        GUARDRAIL_TRIGGERS_TOTAL.labels(flag_type=flag_type, severity=severity).inc()


def record_tool_call(tool_name: str, success: bool, cache_hit: bool, latency_ms: int) -> None:
    """Record metrics for a single tool invocation.

    Called from ToolRegistry.execute().
    """
    TOOL_CALLS_TOTAL.labels(
        tool_name=tool_name,
        cache_hit=str(cache_hit).lower(),
        success=str(success).lower(),
    ).inc()
    TOOL_LATENCY_SECONDS.labels(tool_name=tool_name).observe(latency_ms / 1000.0)


def record_cache_lookup(key_kind: str, tier: str) -> None:
    """Record which tier answered a market data cache lookup.

    Called from app/services/cache.py.
    """
    MARKET_CACHE_REQUESTS_TOTAL.labels(key_kind=key_kind, tier=tier).inc()


def record_cache_fetch(key_kind: str, seconds: float) -> None:
    """Record how long an upstream fetch took, on cache misses only."""
    MARKET_FETCH_SECONDS.labels(key_kind=key_kind).observe(seconds)
