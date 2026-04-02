"""Analysis API endpoints.

POST /analysis          -- trigger a new multi-agent analysis run
GET  /analysis/{run_id} -- get the completed result
GET  /analysis/{run_id}/stream -- SSE stream of live agent progress events
GET  /analysis/history  -- paginated user analysis history
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import AsyncIterator, Optional

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.agents.base.schemas import UserPreferences
from app.agents.orchestrator.graph import run_analysis
from app.config import get_settings
from app.db.repositories import (
    create_run,
    ensure_user,
    get_run,
    list_runs,
    update_run_error,
    update_run_result,
)
from app.db.session import AsyncSessionLocal, get_db
from app.memory.session import add_recent_ticker

log = structlog.get_logger(__name__)
router = APIRouter()


# -- Request / Response models ------------------------------------------------


class AnalysisRequest(BaseModel):
    ticker: str
    query: str = "Provide a comprehensive analysis"
    user_id: str = "anonymous"
    session_id: Optional[str] = None
    depth: Optional[str] = None  # override analysis_depth
    risk_tolerance: Optional[str] = None


class AnalysisResponse(BaseModel):
    run_id: str
    status: str
    ticker: str
    created_at: str


# -- Endpoints ----------------------------------------------------------------


@router.post("", response_model=AnalysisResponse)
async def trigger_analysis(
    req: AnalysisRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Trigger a new multi-agent analysis.  Returns run_id immediately."""
    run_id = str(uuid.uuid4())

    await ensure_user(db, req.user_id)
    run = await create_run(db, run_id, req.user_id, req.ticker, req.query)

    # Track recently analysed tickers (fire-and-forget, non-blocking)
    asyncio.create_task(add_recent_ticker(req.user_id, req.ticker))

    prefs = UserPreferences(
        user_id=req.user_id,
        risk_tolerance=req.risk_tolerance or "moderate",
        analysis_depth=req.depth or "standard",
    )

    async def _run():
        """Background task -- uses its own DB session (request scope is gone)."""
        async with AsyncSessionLocal() as session:
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
                result = {
                    "final_report": report.model_dump() if report else None,
                    "agent_results": {
                        k: state[k].model_dump() if state.get(k) else None
                        for k in [
                            "market_research_result",
                            "sentiment_result",
                            "fundamental_result",
                            "technical_result",
                            "risk_result",
                        ]
                    },
                    "guardrail_flags": [
                        f.model_dump() if hasattr(f, "model_dump") else f
                        for f in state.get("guardrail_flags", [])
                    ],
                    "hallucination_score": state.get("hallucination_score", 0.0),
                    "latency_breakdown": state.get("latency_breakdown", {}),
                    "required_agents": state.get("required_agents", []),
                }
                await update_run_result(session, run_id, result)
                await session.commit()
            except Exception as e:
                log.error("analysis_background_error", run_id=run_id, error=str(e))
                await update_run_error(session, run_id, str(e))
                await session.commit()

    background_tasks.add_task(_run)

    return AnalysisResponse(
        run_id=run_id,
        status="running",
        ticker=req.ticker.upper(),
        created_at=run.created_at.isoformat() if run.created_at else "",
    )


@router.get("/history")
async def get_history(
    user_id: str = Query("anonymous"),
    limit: int = Query(20, le=100),
    offset: int = Query(0),
    db: AsyncSession = Depends(get_db),
):
    """Return paginated analysis history for a user."""
    runs, total = await list_runs(db, user_id, limit=limit, offset=offset)
    return {
        "total": total,
        "items": [
            {k: v for k, v in r.to_dict().items() if k != "result"} for r in runs
        ],
    }


@router.get("/{run_id}")
async def get_analysis_result(run_id: str, db: AsyncSession = Depends(get_db)):
    """Get the completed analysis result by run_id."""
    run = await get_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return run.to_dict()


@router.get("/{run_id}/stream")
async def stream_analysis(run_id: str):
    """SSE stream that emits agent progress events and the final result.

    Events:
      agent_started    -- an agent began running
      agent_completed  -- an agent finished
      guardrails_passed -- guardrails check done
      synthesis_started -- synthesiser running
      complete         -- full result ready (includes final_report JSON)
      error            -- something went wrong
    """

    async def _event_generator() -> AsyncIterator[dict]:
        # Wait briefly for the run to appear in the DB
        run = None
        for _ in range(50):
            async with AsyncSessionLocal() as session:
                run = await get_run(session, run_id)
            if run is not None:
                break
            await asyncio.sleep(0.1)

        if run is None:
            yield {
                "event": "error",
                "data": json.dumps({"message": f"Run {run_id} not found"}),
            }
            return

        yield {
            "event": "run_started",
            "data": json.dumps({"run_id": run_id, "ticker": run.ticker}),
        }

        # Poll DB until the run completes (max ~120s at 0.5s intervals)
        for _ in range(240):
            await asyncio.sleep(0.5)
            async with AsyncSessionLocal() as session:
                current = await get_run(session, run_id)

            if current is None:
                continue

            if current.status == "completed":
                result = current.to_dict().get("result") or {}

                # Emit per-agent completion events
                agent_results = result.get("agent_results", {})
                for agent_key, agent_result in agent_results.items():
                    if agent_result:
                        yield {
                            "event": "agent_completed",
                            "data": json.dumps(
                                {
                                    "agent": agent_key.replace("_result", ""),
                                    "confidence": agent_result.get("confidence", 0),
                                }
                            ),
                        }

                yield {
                    "event": "guardrails_passed",
                    "data": json.dumps(
                        {
                            "hallucination_score": result.get(
                                "hallucination_score", 0
                            ),
                            "flags": len(result.get("guardrail_flags", [])),
                        }
                    ),
                }
                yield {"event": "synthesis_started", "data": "{}"}

                yield {
                    "event": "complete",
                    "data": json.dumps(
                        {
                            "run_id": run_id,
                            "status": "completed",
                            "result": result,
                        }
                    ),
                }
                return

            elif current.status == "error":
                yield {
                    "event": "error",
                    "data": json.dumps(
                        {"message": current.error or "Unknown error"}
                    ),
                }
                return

        yield {
            "event": "error",
            "data": json.dumps({"message": "Timeout waiting for analysis"}),
        }

    return EventSourceResponse(_event_generator())
