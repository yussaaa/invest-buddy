"""Async repository functions — thin data-access layer over SQLAlchemy models.

All functions accept an AsyncSession and return ORM model instances.
The API layer calls `.to_dict()` on the results for serialisation.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AnalysisRun, Feedback, User, UserPreferencesRow, Watchlist


# ── Users ────────────────────────────────────────────────────────────────────


async def ensure_user(session: AsyncSession, user_id: str) -> User:
    """Get or create a user row.  Lightweight — just ensures FK integrity.

    Uses INSERT ... ON CONFLICT DO NOTHING to handle concurrent requests
    that both try to create the same user simultaneously.
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    stmt = pg_insert(User).values(id=user_id).on_conflict_do_nothing(index_elements=["id"])
    await session.execute(stmt)
    await session.flush()

    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one()
    user.last_active = datetime.now(timezone.utc)
    return user


# ── Analysis Runs ────────────────────────────────────────────────────────────


async def create_run(
    session: AsyncSession,
    run_id: str,
    user_id: str,
    ticker: str,
    query: str,
) -> AnalysisRun:
    """Insert a new analysis run with status='running'."""
    run = AnalysisRun(
        run_id=run_id,
        user_id=user_id,
        ticker=ticker.upper(),
        query=query,
        status="running",
    )
    session.add(run)
    await session.flush()
    return run


async def update_run_result(session: AsyncSession, run_id: str, result: dict) -> None:
    """Update a run with the completed analysis result.

    ``result`` dict keys: final_report, agent_results, guardrail_flags,
    hallucination_score, latency_breakdown, required_agents.
    """
    row = await session.get(AnalysisRun, run_id)
    if row is None:
        return
    row.status = "completed"
    row.final_report = result.get("final_report")
    row.agent_results = result.get("agent_results")
    row.guardrail_flags = result.get("guardrail_flags")
    row.hallucination_score = result.get("hallucination_score")
    row.latency_breakdown = result.get("latency_breakdown")
    row.required_agents = result.get("required_agents")
    row.updated_at = datetime.now(timezone.utc)


async def update_run_error(session: AsyncSession, run_id: str, error: str) -> None:
    """Mark a run as failed with an error message."""
    row = await session.get(AnalysisRun, run_id)
    if row is None:
        return
    row.status = "error"
    row.error = error
    row.updated_at = datetime.now(timezone.utc)


async def get_run(session: AsyncSession, run_id: str) -> AnalysisRun | None:
    """Fetch a single run by ID."""
    return await session.get(AnalysisRun, run_id)


async def list_runs(
    session: AsyncSession,
    user_id: str,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[AnalysisRun], int]:
    """Return (runs, total_count) ordered by created_at DESC."""
    base = select(AnalysisRun).where(AnalysisRun.user_id == user_id)

    # Total count
    count_q = select(func.count()).select_from(base.subquery())
    total = (await session.execute(count_q)).scalar_one()

    # Paginated results
    rows_q = base.order_by(AnalysisRun.created_at.desc()).offset(offset).limit(limit)
    rows = (await session.execute(rows_q)).scalars().all()

    return list(rows), total


# ── Watchlists ───────────────────────────────────────────────────────────────


async def create_watchlist(
    session: AsyncSession,
    user_id: str,
    name: str,
    tickers: list[str],
    notes: dict | None = None,
) -> Watchlist:
    wl = Watchlist(
        user_id=user_id,
        name=name,
        tickers=[t.upper() for t in tickers],
        notes=notes or {},
    )
    session.add(wl)
    await session.flush()
    return wl


async def get_watchlists(session: AsyncSession, user_id: str) -> list[Watchlist]:
    q = select(Watchlist).where(Watchlist.user_id == user_id).order_by(Watchlist.created_at)
    return list((await session.execute(q)).scalars().all())


async def update_watchlist(
    session: AsyncSession,
    wl_id: str,
    name: str | None = None,
    tickers: list[str] | None = None,
    notes: dict | None = None,
) -> Watchlist | None:
    wl = await session.get(Watchlist, wl_id)
    if wl is None:
        return None
    if name is not None:
        wl.name = name
    if tickers is not None:
        wl.tickers = [t.upper() for t in tickers]
    if notes is not None:
        wl.notes = notes
    await session.flush()
    return wl


async def delete_watchlist(session: AsyncSession, wl_id: str) -> bool:
    wl = await session.get(Watchlist, wl_id)
    if wl is None:
        return False
    await session.delete(wl)
    await session.flush()
    return True


# ── Preferences ──────────────────────────────────────────────────────────────


async def get_or_create_preferences(
    session: AsyncSession, user_id: str
) -> UserPreferencesRow:
    row = await session.get(UserPreferencesRow, user_id)
    if row is None:
        row = UserPreferencesRow(user_id=user_id)
        session.add(row)
        await session.flush()
    return row


async def update_preferences(
    session: AsyncSession, user_id: str, **kwargs
) -> UserPreferencesRow:
    """Update user preferences.  Only non-None kwargs are applied."""
    row = await get_or_create_preferences(session, user_id)
    for key, value in kwargs.items():
        if value is not None and hasattr(row, key):
            setattr(row, key, value)
    await session.flush()
    return row


# ── Feedback ─────────────────────────────────────────────────────────────────


async def create_feedback(
    session: AsyncSession,
    run_id: str,
    user_id: str,
    score: int,
    comment: str | None = None,
) -> Feedback:
    fb = Feedback(run_id=run_id, user_id=user_id, score=score, comment=comment)
    session.add(fb)
    await session.flush()
    return fb


async def get_feedback_summary(session: AsyncSession) -> dict:
    """Return {total, positive, negative, score}."""
    total_q = select(func.count()).select_from(Feedback)
    total = (await session.execute(total_q)).scalar_one()

    if total == 0:
        return {"total": 0, "positive": 0, "negative": 0, "score": None}

    pos_q = select(func.count()).select_from(Feedback).where(Feedback.score == 1)
    positive = (await session.execute(pos_q)).scalar_one()

    negative = total - positive
    return {
        "total": total,
        "positive": positive,
        "negative": negative,
        "score": round(positive / total, 2),
    }
