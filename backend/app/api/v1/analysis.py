"""Analysis API endpoints.

POST /analysis          — trigger a new multi-agent analysis run
GET  /analysis/{run_id} — get the completed result
GET  /analysis/{run_id}/stream — SSE stream of live agent progress events
GET  /analysis/history  — paginated user analysis history
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from typing import AsyncIterator, Optional

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.agents.base.schemas import UserPreferences
from app.agents.orchestrator.graph import run_analysis
from app.config import get_settings

log = structlog.get_logger(__name__)
router = APIRouter()

# In-memory run store (Phase 1 — replaced by Postgres in Phase 2)
_run_store: dict[str, dict] = {}


# ── Request / Response models ─────────────────────────────────────────────────


class AnalysisRequest(BaseModel):
    ticker: str
    query: str = "Provide a comprehensive analysis"
    user_id: str = "anonymous"
    session_id: Optional[str] = None
    depth: Optional[str] = None   # override analysis_depth
    risk_tolerance: Optional[str] = None


class AnalysisResponse(BaseModel):
    run_id: str
    status: str
    ticker: str
    created_at: str


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("", response_model=AnalysisResponse)
async def trigger_analysis(
    req: AnalysisRequest,
    background_tasks: BackgroundTasks,
):
    """Trigger a new multi-agent analysis. Returns run_id immediately."""
    run_id = str(uuid.uuid4())
    _run_store[run_id] = {
        "run_id": run_id,
        "status": "running",
        "ticker": req.ticker.upper(),
        "query": req.query,
        "user_id": req.user_id,
        "created_at": datetime.now().isoformat(),
        "result": None,
        "error": None,
    }

    prefs = UserPreferences(
        user_id=req.user_id,
        risk_tolerance=req.risk_tolerance or "moderate",
        analysis_depth=req.depth or "standard",
    )

    async def _run():
        try:
            state = await run_analysis(
                query=req.query,
                ticker=req.ticker,
                user_id=req.user_id,
                session_id=req.session_id,
                user_preferences=prefs,
            )
            # Serialise final_report for storage
            report = state.get("final_report")
            _run_store[run_id]["status"] = "completed"
            _run_store[run_id]["result"] = {
                "final_report": report.model_dump() if report else None,
                "agent_results": {
                    k: state[k].model_dump() if state.get(k) else None
                    for k in ["market_research_result", "sentiment_result",
                               "fundamental_result", "technical_result", "risk_result"]
                },
                "guardrail_flags": [
                    f.model_dump() if hasattr(f, "model_dump") else f
                    for f in state.get("guardrail_flags", [])
                ],
                "hallucination_score": state.get("hallucination_score", 0.0),
                "latency_breakdown": state.get("latency_breakdown", {}),
                "required_agents": state.get("required_agents", []),
            }
        except Exception as e:
            log.error("analysis_background_error", run_id=run_id, error=str(e))
            _run_store[run_id]["status"] = "error"
            _run_store[run_id]["error"] = str(e)

    background_tasks.add_task(_run)

    return AnalysisResponse(
        run_id=run_id,
        status="running",
        ticker=req.ticker.upper(),
        created_at=_run_store[run_id]["created_at"],
    )


@router.get("/history")
async def get_history(
    user_id: str = Query("anonymous"),
    limit: int = Query(20, le=100),
    offset: int = Query(0),
):
    """Return paginated analysis history for a user."""
    user_runs = [
        {k: v for k, v in run.items() if k != "result"}
        for run in _run_store.values()
        if run.get("user_id") == user_id
    ]
    user_runs.sort(key=lambda r: r["created_at"], reverse=True)
    return {
        "total": len(user_runs),
        "items": user_runs[offset : offset + limit],
    }


@router.get("/{run_id}")
async def get_analysis_result(run_id: str):
    """Get the completed analysis result by run_id."""
    run = _run_store.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return run


@router.get("/{run_id}/stream")
async def stream_analysis(run_id: str):
    """SSE stream that emits agent progress events and the final result.

    Events:
      agent_started    → an agent began running
      agent_completed  → an agent finished
      guardrails_passed → guardrails check done
      synthesis_started → synthesiser running
      complete         → full result ready (includes final_report JSON)
      error            → something went wrong
    """
    async def _event_generator() -> AsyncIterator[dict]:
        # Wait for the run to exist
        for _ in range(50):
            if run_id in _run_store:
                break
            await asyncio.sleep(0.1)

        run = _run_store.get(run_id)
        if not run:
            yield {"event": "error", "data": json.dumps({"message": f"Run {run_id} not found"})}
            return

        yield {"event": "run_started", "data": json.dumps({"run_id": run_id, "ticker": run["ticker"]})}

        # Poll until the run completes (max 120s)
        for _ in range(1200):
            await asyncio.sleep(0.1)
            current = _run_store.get(run_id, {})
            status = current.get("status")

            if status == "completed":
                result = current.get("result", {})

                # Emit per-agent completion events
                agent_results = result.get("agent_results", {})
                for agent_key, agent_result in agent_results.items():
                    if agent_result:
                        yield {
                            "event": "agent_completed",
                            "data": json.dumps({
                                "agent": agent_key.replace("_result", ""),
                                "confidence": agent_result.get("confidence", 0),
                            }),
                        }

                yield {"event": "guardrails_passed", "data": json.dumps({
                    "hallucination_score": result.get("hallucination_score", 0),
                    "flags": len(result.get("guardrail_flags", [])),
                })}
                yield {"event": "synthesis_started", "data": "{}"}

                yield {
                    "event": "complete",
                    "data": json.dumps({
                        "run_id": run_id,
                        "status": "completed",
                        "result": result,
                    }),
                }
                return

            elif status == "error":
                yield {
                    "event": "error",
                    "data": json.dumps({"message": current.get("error", "Unknown error")}),
                }
                return

        yield {"event": "error", "data": json.dumps({"message": "Timeout waiting for analysis"})}

    return EventSourceResponse(_event_generator())
