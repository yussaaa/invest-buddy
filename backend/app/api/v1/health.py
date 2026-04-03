"""Health and readiness endpoints for Kubernetes probes."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def liveness():
    """Liveness probe — returns 200 if the process is alive."""
    return {"status": "ok"}


@router.get("/ready")
async def readiness():
    """Readiness probe — checks all backing services.

    Checks: PostgreSQL (+ pgvector), Redis.
    Note: Qdrant was removed in Phase 3 — vector store is pgvector inside Postgres.
    """
    checks = {"api": "ok"}

    # Check Postgres (includes pgvector)
    try:
        from app.db.session import get_db
        async for db in get_db():
            await db.execute(__import__("sqlalchemy").text("SELECT 1"))
            checks["postgres"] = "ok"
            break
    except Exception as e:
        checks["postgres"] = f"error: {str(e)[:50]}"

    # Check Redis
    try:
        from app.cache.redis_client import get_redis
        r = await get_redis()
        await r.ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {str(e)[:50]}"

    all_ok = all(v == "ok" for v in checks.values())
    return {"status": "ready" if all_ok else "degraded", "checks": checks}
