"""LangGraph StateGraph — the architectural centrepiece of the system.

Graph structure:
  classifier_node
       ↓
  rag_prefetch_node  (stub for Phase 1 — full RAG in Phase 3)
       ↓
  [parallel fan-out via Send]
  market_research_node | sentiment_node | fundamental_node | technical_node | risk_node
       ↓
  guardrails_node
       ↓
  synthesiser_node
       ↓
  persistence_node

The parallel fan-out is the key performance feature: all 5 agents run
concurrently. Total latency ≈ slowest single agent, not sum of all.

LangGraph's Send primitive dispatches each agent as an independent node
invocation, all sharing the same AgentState (with reducer annotations for
safe concurrent writes).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Literal

import structlog
from langgraph.graph import END, StateGraph
from langgraph.types import Send

from app.agents.base.guardrails import run_guardrails
from app.agents.base.schemas import AgentResult, UserPreferences
from app.agents.base.state import AgentState
from app.agents.fundamental.agent import FundamentalAnalysisAgent
from app.agents.market_research.agent import MarketResearchAgent
from app.agents.orchestrator.router import classify_query
from app.agents.orchestrator.synthesizer import synthesise_results
from app.agents.risk.agent import RiskAssessmentAgent
from app.agents.sentiment.agent import SentimentAnalysisAgent
from app.agents.technical.agent import TechnicalAnalysisAgent
from app.models.factory import get_provider

log = structlog.get_logger(__name__)

# ── Agent singletons (instantiated once, reused across requests) ─────────────
_fundamental_agent = FundamentalAnalysisAgent()
_sentiment_agent = SentimentAnalysisAgent()
_technical_agent = TechnicalAnalysisAgent()
_risk_agent = RiskAssessmentAgent()
_market_research_agent = MarketResearchAgent()


# ── Node functions ────────────────────────────────────────────────────────────


async def classifier_node(state: AgentState) -> dict:
    """Classify the query and determine which agents to run."""
    t0 = time.monotonic()
    classification = await classify_query(
        query=state["query"],
        ticker_hint=state.get("ticker"),
        user_preferences=state.get("user_preferences"),
    )
    return {
        "classification": classification,
        "ticker": classification.primary_ticker or state.get("ticker", ""),
        "required_agents": classification.required_agents,
        "analysis_depth": classification.analysis_depth,
        "latency_breakdown": {"classifier": round(time.monotonic() - t0, 3)},
    }


async def rag_prefetch_node(state: AgentState) -> dict:
    """Pre-fetch RAG context before parallel agent execution.

    Behavior depends on state["use_rag"]:
      - False: skip entirely, return empty context (fast path)
      - True: run inline ingestion if needed, then 3-stage retrieval

    Inline ingestion:
      1. Check if ticker is already indexed in pgvector
      2. If not → run ingest_ticker() to download SEC filings + news → chunk → embed → upsert
      3. If user uploaded files → ingest those too
      4. Then run the normal 3-stage retrieval pipeline
    """
    use_rag = state.get("use_rag", False)
    if not use_rag:
        log.info("rag_skipped", ticker=state.get("ticker"))
        return {
            "retrieved_documents": [],
            "entity_graph": {},
        }

    from app.rag.retrieval.pipeline import get_rag_pipeline
    from app.rag.context.builder import build_context
    from app.rag.ingestion.ingest import check_ticker_indexed, ingest_ticker, ingest_custom_files

    t0 = time.monotonic()
    ticker = state.get("ticker", "")

    try:
        # Step 1: Inline ingestion if ticker not yet indexed
        if not await check_ticker_indexed(ticker):
            log.info("rag_inline_ingestion_start", ticker=ticker)
            result = await ingest_ticker(ticker)
            log.info("rag_inline_ingestion_done", ticker=ticker, **result)

        # Step 2: Ingest user-uploaded files if any
        file_ids = state.get("file_ids", [])
        if file_ids:
            from app.api.v1.upload import get_upload_path
            file_paths = [get_upload_path(fid) for fid in file_ids]
            file_paths = [p for p in file_paths if p]  # filter None
            if file_paths:
                log.info("rag_custom_ingestion_start", ticker=ticker, files=len(file_paths))
                await ingest_custom_files(ticker, file_paths, user_id=state.get("user_id", "anonymous"))

        # Step 3: 3-stage retrieval
        pipeline = get_rag_pipeline()
        docs = await pipeline.retrieve(query=state.get("query", ""), ticker=ticker)
        build_context(docs)  # validate context builds

        log.info(
            "rag_prefetch_complete",
            ticker=ticker,
            docs_retrieved=len(docs),
            latency_s=round(time.monotonic() - t0, 3),
        )
        return {
            "retrieved_documents": docs,
            "entity_graph": {},
            "latency_breakdown": {"rag_prefetch": round(time.monotonic() - t0, 3)},
        }
    except Exception as e:
        log.warning("rag_prefetch_fallback", error=str(e))
        return {
            "retrieved_documents": [],
            "entity_graph": {},
        }


async def market_research_node(state: AgentState) -> dict:
    """Market Research specialist agent node."""
    if "market_research" not in state.get("required_agents", []):
        return {}
    t0 = time.monotonic()
    result = await _market_research_agent.run(state)
    return {
        "market_research_result": result,
        "latency_breakdown": {"market_research": round(time.monotonic() - t0, 3)},
        "tool_calls_made": [tc.model_dump() for tc in result.tool_calls],
    }


async def sentiment_node(state: AgentState) -> dict:
    """Sentiment Analysis specialist agent node."""
    if "sentiment" not in state.get("required_agents", []):
        return {}
    t0 = time.monotonic()
    result = await _sentiment_agent.run(state)
    return {
        "sentiment_result": result,
        "latency_breakdown": {"sentiment": round(time.monotonic() - t0, 3)},
        "tool_calls_made": [tc.model_dump() for tc in result.tool_calls],
    }


async def fundamental_node(state: AgentState) -> dict:
    """Fundamental Analysis specialist agent node."""
    if "fundamental" not in state.get("required_agents", []):
        return {}
    t0 = time.monotonic()
    result = await _fundamental_agent.run(state)
    return {
        "fundamental_result": result,
        "latency_breakdown": {"fundamental": round(time.monotonic() - t0, 3)},
        "tool_calls_made": [tc.model_dump() for tc in result.tool_calls],
    }


async def technical_node(state: AgentState) -> dict:
    """Technical Analysis specialist agent node."""
    if "technical" not in state.get("required_agents", []):
        return {}
    t0 = time.monotonic()
    result = await _technical_agent.run(state)
    return {
        "technical_result": result,
        "latency_breakdown": {"technical": round(time.monotonic() - t0, 3)},
        "tool_calls_made": [tc.model_dump() for tc in result.tool_calls],
    }


async def risk_node(state: AgentState) -> dict:
    """Risk Assessment specialist agent node."""
    if "risk" not in state.get("required_agents", []):
        return {}
    t0 = time.monotonic()
    result = await _risk_agent.run(state)
    return {
        "risk_result": result,
        "latency_breakdown": {"risk": round(time.monotonic() - t0, 3)},
        "tool_calls_made": [tc.model_dump() for tc in result.tool_calls],
    }


async def guardrails_node(state: AgentState) -> dict:
    """Check all agent results for hallucinations, advice, missing citations."""
    t0 = time.monotonic()
    provider = get_provider()
    updated_state = await run_guardrails(state, provider)
    return {
        "guardrail_flags": updated_state.get("guardrail_flags", []),
        "hallucination_score": updated_state.get("hallucination_score", 0.0),
        "latency_breakdown": {"guardrails": round(time.monotonic() - t0, 3)},
    }


async def synthesiser_node(state: AgentState) -> dict:
    """Merge all AgentResults into a FinalReport."""
    t0 = time.monotonic()

    # Collect all available agent results
    results: list[AgentResult] = []
    for key in ["market_research_result", "sentiment_result", "fundamental_result",
                "technical_result", "risk_result"]:
        r = state.get(key)
        if r is not None:
            results.append(r)

    final_report = await synthesise_results(
        agent_results=results,
        ticker=state.get("ticker", ""),
        query=state.get("query", ""),
        user_preferences=state.get("user_preferences"),
    )

    return {
        "final_report": final_report,
        "disclaimer_appended": True,
        "latency_breakdown": {"synthesiser": round(time.monotonic() - t0, 3)},
    }


async def persistence_node(state: AgentState) -> dict:
    """Save analysis to DB and emit metrics. Stub — full impl in Phase 2."""
    report = state.get("final_report")
    log.info(
        "analysis_complete",
        run_id=state.get("run_id"),
        ticker=state.get("ticker"),
        confidence=report.overall_confidence if report else None,
        agents_run=state.get("required_agents"),
    )
    return {}


# ── Fan-out router — decides parallel execution ──────────────────────────────


def route_to_agents(state: AgentState) -> list[Send]:
    """Return Send objects for all required agents — they run in parallel."""
    agent_nodes = {
        "market_research": "market_research_node",
        "sentiment": "sentiment_node",
        "fundamental": "fundamental_node",
        "technical": "technical_node",
        "risk": "risk_node",
    }
    required = state.get("required_agents", list(agent_nodes.keys()))
    return [
        Send(agent_nodes[agent], state)
        for agent in required
        if agent in agent_nodes
    ]


# ── Build the graph ───────────────────────────────────────────────────────────


def build_graph() -> Any:
    """Construct and compile the LangGraph StateGraph."""
    builder = StateGraph(AgentState)

    # Add all nodes
    builder.add_node("classifier_node", classifier_node)
    builder.add_node("rag_prefetch_node", rag_prefetch_node)
    builder.add_node("market_research_node", market_research_node)
    builder.add_node("sentiment_node", sentiment_node)
    builder.add_node("fundamental_node", fundamental_node)
    builder.add_node("technical_node", technical_node)
    builder.add_node("risk_node", risk_node)
    builder.add_node("guardrails_node", guardrails_node)
    builder.add_node("synthesiser_node", synthesiser_node)
    builder.add_node("persistence_node", persistence_node)

    # Linear flow up to rag_prefetch
    builder.set_entry_point("classifier_node")
    builder.add_edge("classifier_node", "rag_prefetch_node")

    # Fan-out: rag_prefetch dispatches all required agents in parallel
    builder.add_conditional_edges(
        "rag_prefetch_node",
        route_to_agents,
        # All agent nodes can be targets of the fan-out
        [
            "market_research_node",
            "sentiment_node",
            "fundamental_node",
            "technical_node",
            "risk_node",
        ],
    )

    # All agent nodes converge on guardrails
    for node in ["market_research_node", "sentiment_node", "fundamental_node",
                 "technical_node", "risk_node"]:
        builder.add_edge(node, "guardrails_node")

    # Sequential from guardrails onward
    builder.add_edge("guardrails_node", "synthesiser_node")
    builder.add_edge("synthesiser_node", "persistence_node")
    builder.add_edge("persistence_node", END)

    return builder.compile()


# Compiled graph singleton
_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


# ── Public API ────────────────────────────────────────────────────────────────


async def run_analysis(
    query: str,
    ticker: str,
    user_id: str = "anonymous",
    session_id: str | None = None,
    user_preferences: UserPreferences | None = None,
    use_rag: bool = False,
    file_ids: list[str] | None = None,
) -> AgentState:
    """Run the full multi-agent analysis pipeline.

    Args:
        use_rag: If True, runs inline ingestion (if needed) + 3-stage retrieval
        file_ids: User-uploaded file IDs to include in RAG context

    Returns the final AgentState containing the FinalReport and all intermediate
    agent results. Callers can stream this or return it as JSON.
    """
    run_id = str(uuid.uuid4())
    if session_id is None:
        session_id = str(uuid.uuid4())

    initial_state: AgentState = {
        "query": query,
        "ticker": ticker.upper(),
        "user_id": user_id,
        "session_id": session_id,
        "user_preferences": user_preferences or UserPreferences(user_id=user_id),
        "classification": None,
        "required_agents": [],
        "analysis_depth": "standard",
        "use_rag": use_rag,
        "file_ids": file_ids or [],
        "retrieved_documents": [],
        "entity_graph": {},
        "market_research_result": None,
        "sentiment_result": None,
        "fundamental_result": None,
        "technical_result": None,
        "risk_result": None,
        "guardrail_flags": [],
        "hallucination_score": 0.0,
        "all_citations": [],
        "final_report": None,
        "disclaimer_appended": False,
        "tool_calls_made": [],
        "latency_breakdown": {},
        "run_id": run_id,
        "error": None,
    }

    graph = get_graph()
    t0 = time.monotonic()

    try:
        final_state = await graph.ainvoke(initial_state)
        total_latency = round(time.monotonic() - t0, 3)
        final_state["latency_breakdown"]["total"] = total_latency
        log.info(
            "analysis_complete",
            run_id=run_id,
            ticker=ticker,
            total_latency_s=total_latency,
        )
        return final_state
    except Exception as e:
        log.error("analysis_failed", run_id=run_id, ticker=ticker, error=str(e))
        initial_state["error"] = str(e)
        return initial_state
