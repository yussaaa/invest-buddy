# Agent Invest — Project Memory

## Project Overview
Multi-agent stock investment analysis platform. Portfolio project for Senior AI Engineer roles.
- **Backend**: Python 3.11, FastAPI, LangGraph, SQLAlchemy async, PostgreSQL, Redis
- **Frontend**: React 18, Vite, TypeScript, Tailwind CSS
- **Run**: `cd backend && source .venv/bin/activate && uvicorn app.main:app --reload`

## Phase Status
- Phase 1 ✅ — Full working system (5 agents, 22 tools, LangGraph, SSE, React frontend)
- Phase 2 ✅ — DB persistence, Redis session, retry logic, Alembic migrations, integration tests
- Phase 3 → Next: RAG pipeline (Qdrant, doc ingestion, multi-vector retrieval)

## Key Architecture
- `backend/app/agents/orchestrator/graph.py` — LangGraph StateGraph (lazy compile via `get_graph()`)
- `backend/app/db/` — session.py, models.py, repositories.py
- `backend/app/tools/retry_decorator.py` — tenacity `retry_network_errors` decorator
- `backend/alembic/` — async alembic setup (NullPool + asyncio.run)

## Alembic Setup
- `backend/alembic.ini` + `backend/alembic/` (NOT inside app/db/)
- Run from `backend/`: `alembic upgrade head`
- Async env.py pattern: `create_async_engine(url, poolclass=NullPool)` + `asyncio.run()`
- Initial migration: `backend/alembic/versions/001_initial_schema.py`

## Integration Test Strategy
- DB: PostgreSQL + nested transaction rollback (no SQLite — models use JSONB/ARRAY)
- `join_transaction_mode="create_savepoint"` in AsyncSession bound to test connection
- Conftest: `db_engine` (session) → `db_connection` (function) → `db_session` → `app` → `client`
- App fixture: minimal FastAPI (no lifespan — skips LangGraph compilation)
- Run: `pytest tests/integration/ -m integration`

## Common Issues
- `.env` must be at project root (config.py walks up from its location)
- `pandas-ta` not available on Python 3.11; use `ta` library instead
- Makefile uses hard tabs
- LangGraph graph is lazy — only compiled on first `get_graph()` call, not at import
