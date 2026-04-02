"""Integration tests for analysis run management and feedback endpoints.

We do NOT trigger real LangGraph analysis — that requires LLM API keys and is slow.
Instead:
- `test_create_analysis_run` patches `run_analysis` to prevent LLM calls
- Other tests manipulate the DB directly via repositories for test setup
- Feedback tests require a run to exist (FK constraint)
"""
from __future__ import annotations

import uuid
from unittest import mock

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories import create_run, ensure_user, update_run_result


@pytest.mark.integration
async def test_create_analysis_run(client: AsyncClient):
    """POST /analysis creates a new run and returns run_id + status=running."""
    # Patch run_analysis so the background task never calls an LLM
    async def _noop(*args, **kwargs):
        import asyncio
        await asyncio.sleep(9999)  # never completes during the test

    with mock.patch("app.api.v1.analysis.run_analysis", side_effect=_noop):
        response = await client.post(
            "/api/v1/analysis",
            json={"ticker": "AAPL", "query": "Test analysis", "user_id": "user-ana-01"},
        )

    assert response.status_code == 200
    data = response.json()
    assert "run_id" in data
    assert data["status"] == "running"
    assert data["ticker"] == "AAPL"
    assert "created_at" in data


@pytest.mark.integration
async def test_get_analysis_run_not_found(client: AsyncClient):
    """GET /analysis/{run_id} returns 404 for a non-existent run."""
    fake_id = str(uuid.uuid4())
    response = await client.get(f"/api/v1/analysis/{fake_id}")
    assert response.status_code == 404


@pytest.mark.integration
async def test_get_analysis_run_status(
    client: AsyncClient, db_session: AsyncSession
):
    """GET /analysis/{run_id} returns the run with its current status."""
    await ensure_user(db_session, "user-ana-02")
    run_id = str(uuid.uuid4())
    await create_run(db_session, run_id, "user-ana-02", "MSFT", "Status test")
    await db_session.flush()

    response = await client.get(f"/api/v1/analysis/{run_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["run_id"] == run_id
    assert data["ticker"] == "MSFT"
    assert data["status"] == "running"


@pytest.mark.integration
async def test_get_completed_analysis_result(
    client: AsyncClient, db_session: AsyncSession
):
    """GET /analysis/{run_id} includes result dict when status=completed."""
    await ensure_user(db_session, "user-ana-03")
    run_id = str(uuid.uuid4())
    await create_run(db_session, run_id, "user-ana-03", "GOOGL", "Completed test")
    await db_session.flush()

    mock_result = {
        "final_report": {"summary": "Test summary", "recommendation": "BUY"},
        "agent_results": {},
        "guardrail_flags": [],
        "hallucination_score": 0.05,
        "latency_breakdown": {},
        "required_agents": ["fundamental"],
    }
    await update_run_result(db_session, run_id, mock_result)
    await db_session.flush()

    response = await client.get(f"/api/v1/analysis/{run_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert data["result"] is not None
    assert data["result"]["final_report"]["recommendation"] == "BUY"


@pytest.mark.integration
async def test_history_pagination(client: AsyncClient, db_session: AsyncSession):
    """GET /analysis/history returns paginated results."""
    await ensure_user(db_session, "user-ana-04")
    for i in range(5):
        run_id = str(uuid.uuid4())
        await create_run(db_session, run_id, "user-ana-04", f"TK{i}", f"Query {i}")
    await db_session.flush()

    resp1 = await client.get(
        "/api/v1/analysis/history",
        params={"user_id": "user-ana-04", "limit": 3, "offset": 0},
    )
    assert resp1.status_code == 200
    page1 = resp1.json()
    assert page1["total"] == 5
    assert len(page1["items"]) == 3

    resp2 = await client.get(
        "/api/v1/analysis/history",
        params={"user_id": "user-ana-04", "limit": 3, "offset": 3},
    )
    assert resp2.status_code == 200
    page2 = resp2.json()
    assert len(page2["items"]) == 2
    assert page2["total"] == 5

    # No overlap between pages
    ids1 = {item["run_id"] for item in page1["items"]}
    ids2 = {item["run_id"] for item in page2["items"]}
    assert ids1.isdisjoint(ids2)


@pytest.mark.integration
async def test_history_empty_for_unknown_user(client: AsyncClient):
    """GET /analysis/history returns empty result for a user with no runs."""
    resp = await client.get(
        "/api/v1/analysis/history",
        params={"user_id": "no-such-user-ever-99999"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []


@pytest.mark.integration
async def test_submit_feedback(client: AsyncClient, db_session: AsyncSession):
    """POST /feedback records a thumbs-up against a completed run."""
    await ensure_user(db_session, "user-ana-05")
    run_id = str(uuid.uuid4())
    await create_run(db_session, run_id, "user-ana-05", "AMZN", "Feedback test")
    await update_run_result(db_session, run_id, {
        "final_report": {}, "agent_results": {}, "guardrail_flags": [],
        "hallucination_score": 0.0, "latency_breakdown": {}, "required_agents": [],
    })
    await db_session.flush()

    response = await client.post(
        "/api/v1/feedback",
        json={
            "run_id": run_id,
            "user_id": "user-ana-05",
            "score": 1,
            "comment": "Great analysis!",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "recorded"


@pytest.mark.integration
async def test_submit_feedback_invalid_score(
    client: AsyncClient, db_session: AsyncSession
):
    """POST /feedback rejects scores other than -1 or 1."""
    await ensure_user(db_session, "user-ana-06")
    run_id = str(uuid.uuid4())
    await create_run(db_session, run_id, "user-ana-06", "TSLA", "Score validation test")
    await db_session.flush()

    response = await client.post(
        "/api/v1/feedback",
        json={"run_id": run_id, "user_id": "user-ana-06", "score": 5},
    )
    assert response.status_code == 400


@pytest.mark.integration
async def test_feedback_summary(client: AsyncClient, db_session: AsyncSession):
    """GET /feedback/summary returns aggregate counts across all feedback."""
    await ensure_user(db_session, "user-ana-07")
    run_id = str(uuid.uuid4())
    await create_run(db_session, run_id, "user-ana-07", "NVDA", "Summary test")
    await update_run_result(db_session, run_id, {
        "final_report": {}, "agent_results": {}, "guardrail_flags": [],
        "hallucination_score": 0.0, "latency_breakdown": {}, "required_agents": [],
    })
    await db_session.flush()

    # Submit two positive, one negative
    for score in [1, 1, -1]:
        await client.post(
            "/api/v1/feedback",
            json={"run_id": run_id, "user_id": "user-ana-07", "score": score},
        )

    resp = await client.get("/api/v1/feedback/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 3
    assert data["positive"] >= 2
    assert data["negative"] >= 1
    assert data["score"] is not None
    assert 0 <= data["score"] <= 1
