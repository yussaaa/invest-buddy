"""LangGraph StateGraph — the architectural centrepiece of the system.

Graph structure:
  classifier_node
       ↓
  rag_prefetch_node  (ingest-if-needed, then 3-stage retrieval; skipped
       ↓              entirely when state["use_rag"] is False)
  [parallel fan-out via Send — the router picks which specialists a query needs]
  market_research_node | sentiment_node | fundamental_node | technical_node
                       | risk_node | options_node
       ↓
  guardrails_node
       ↓
  synthesiser_node
       ↓
  persistence_node

The parallel fan-out is the key performance feature: the selected specialists
run concurrently. Total latency ≈ slowest single agent, not sum of all.

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
from app.agents.options.agent import OptionsStrategyAgent
from app.agents.technical.agent import TechnicalAnalysisAgent
from app.models.factory import get_provider

log = structlog.get_logger(__name__)

# ── Agent singletons (instantiated once, reused across requests) ─────────────
_fundamental_agent = FundamentalAnalysisAgent()
_sentiment_agent = SentimentAnalysisAgent()
_technical_agent = TechnicalAnalysisAgent()
_risk_agent = RiskAssessmentAgent()
_market_research_agent = MarketResearchAgent()
_options_agent = OptionsStrategyAgent()


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


async def options_node(state: AgentState) -> dict:
    """Options Strategy specialist agent node."""
    if "options" not in state.get("required_agents", []):
        return {}
    t0 = time.monotonic()
    result = await _options_agent.run(state)
    return {
        "options_result": result,
        "latency_breakdown": {"options": round(time.monotonic() - t0, 3)},
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
                "technical_result", "risk_result", "options_result"]:
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
    """Save analysis to DB, score with RAGAS, log to MLflow, emit Prometheus metrics."""
    from app.observability.metrics import record_analysis_metrics
    from app.observability.monitoring_client import get_monitoring_client
    from app.observability.ragas_evaluator import get_evaluator
    from app.config import get_settings

    report = state.get("final_report")
    ticker = state.get("ticker", "")
    run_id = state.get("run_id", "")

    # 1. Online RAGAS scoring (non-blocking — failures don't break the pipeline)
    eval_scores = {}
    try:
        evaluator = get_evaluator()
        answer = report.detailed_analysis if report else ""
        contexts = [doc.get("text", "") for doc in state.get("retrieved_documents", [])][:5]
        if answer and contexts:
            eval_scores = await evaluator.evaluate_response(
                question=state.get("query", ""),
                answer=answer,
                contexts=contexts if contexts else [answer[:500]],
            )
            log.info("ragas_scored", run_id=run_id, scores=eval_scores)
    except Exception as e:
        log.warning("ragas_scoring_failed", error=str(e))

    # 2. Log to MLflow (non-blocking)
    try:
        settings = get_settings()
        client = get_monitoring_client()

        # Collect prompt versions
        from app.agents.fundamental.prompts import PROMPT_VERSION as fund_v
        from app.agents.sentiment.prompts import PROMPT_VERSION as sent_v
        from app.agents.technical.prompts import PROMPT_VERSION as tech_v
        from app.agents.risk.prompts import PROMPT_VERSION as risk_v
        from app.agents.market_research.prompts import PROMPT_VERSION as mkt_v
        from app.agents.options.prompts import PROMPT_VERSION as opt_v

        params = {
            "ticker": ticker,
            "model_provider": settings.model_provider,
            "fast_model": settings.fast_model,
            "smart_model": settings.smart_model,
            "analysis_depth": state.get("analysis_depth", "standard"),
            "use_rag": str(state.get("use_rag", False)),
            "agents": ",".join(state.get("required_agents", [])),
            "prompt_fundamental": fund_v,
            "prompt_sentiment": sent_v,
            "prompt_technical": tech_v,
            "prompt_risk": risk_v,
            "prompt_market_research": mkt_v,
            "prompt_options": opt_v,
        }
        metrics = {
            "overall_confidence": report.overall_confidence if report else 0,
            "hallucination_score": state.get("hallucination_score", 0),
            "guardrail_flags_count": len(state.get("guardrail_flags", [])),
            **{f"latency_{k}": v for k, v in state.get("latency_breakdown", {}).items()},
            **{f"ragas_{k}": v for k, v in eval_scores.items() if isinstance(v, (int, float)) and v >= 0},
        }
        client.log_analysis_run(run_id, ticker, params, metrics)
        log.info("mlflow_logged", run_id=run_id)
    except Exception as e:
        log.warning("mlflow_logging_failed", error=str(e))

    # 3. Emit Prometheus metrics
    try:
        state_with_eval = dict(state)
        state_with_eval["eval_metadata"] = eval_scores
        record_analysis_metrics(state_with_eval)
    except Exception as e:
        log.warning("prometheus_metrics_failed", error=str(e))

    log.info(
        "analysis_complete",
        run_id=run_id,
        ticker=ticker,
        confidence=report.overall_confidence if report else None,
        agents_run=state.get("required_agents"),
        ragas=eval_scores,
    )
    return {"eval_metadata": eval_scores}


# ── Fan-out router — decides parallel execution ──────────────────────────────


def route_to_agents(state: AgentState) -> list[Send]:
    """Return Send objects for all required agents — they run in parallel."""
    agent_nodes = {
        "market_research": "market_research_node",
        "sentiment": "sentiment_node",
        "fundamental": "fundamental_node",
        "technical": "technical_node",
        "risk": "risk_node",
        "options": "options_node",
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
    builder.add_node("options_node", options_node)
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
            "options_node",
        ],
    )

    # All agent nodes converge on guardrails
    for node in ["market_research_node", "sentiment_node", "fundamental_node",
                 "technical_node", "risk_node", "options_node"]:
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
        "options_result": None,
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
