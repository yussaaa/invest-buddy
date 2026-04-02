"""
Shared pytest fixtures for integration tests.

DB Strategy: Each test runs inside a nested transaction (savepoint).
- `db_connection` wraps each test in an outer transaction (never committed)
- `db_session` uses `join_transaction_mode="create_savepoint"` so that
  session.flush() and session.commit() issue SAVEPOINTs instead of real commits
- After each test, `db_connection` rolls back the outer transaction → DB is clean

Requirements:
  - PostgreSQL must be running at DATABASE_URL (or TEST_DATABASE_URL)
  - Tables must exist: run `alembic upgrade head` once, or start the dev server
    (which auto-creates tables in development mode)

Run integration tests with:
  pytest tests/integration/ -m integration
"""
from __future__ import annotations

import os
from typing import AsyncIterator

import pytest_asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.db.session import Base, get_db

# Allow pointing at a dedicated test DB via env var (recommended for CI)
TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://agent_invest:password@localhost:5432/agent_invest",
)


# ── Engine (session-scoped — created once per pytest session) ─────────────────


@pytest_asyncio.fixture(scope="session")
async def db_engine():
    """Async engine used for all integration tests in this session."""
    from app.db import models  # noqa: F401 — registers all models with Base.metadata

    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)

    # Ensure all tables exist (idempotent)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine
    await engine.dispose()


# ── Connection (function-scoped) — begins outer txn, rolls back after test ────


@pytest_asyncio.fixture
async def db_connection(db_engine) -> AsyncIterator[AsyncConnection]:
    """
    Opens a connection with an active transaction.
    The transaction is NEVER committed — rolled back after the test,
    so no data persists between tests.
    """
    async with db_engine.connect() as conn:
        await conn.begin()
        yield conn
        await conn.rollback()


# ── Session (function-scoped) — bound to test connection ──────────────────────


@pytest_asyncio.fixture
async def db_session(db_connection: AsyncConnection) -> AsyncIterator[AsyncSession]:
    """
    AsyncSession bound to the test connection.

    `join_transaction_mode="create_savepoint"` means:
    - session.flush() → SAVEPOINT (not a real flush to disk)
    - session.commit() → RELEASE SAVEPOINT (not a real commit)
    The outer transaction on db_connection stays open until rollback.
    """
    async with AsyncSession(
        bind=db_connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as session:
        yield session


# ── FastAPI app + get_db override ─────────────────────────────────────────────


@pytest_asyncio.fixture
async def app(db_session: AsyncSession):
    """
    Minimal FastAPI app with the DB dependency overridden to use the test session.

    We skip the full lifespan (which warms up LangGraph and tools) to keep
    tests fast and avoid needing API keys.
    """
    from app.api.v1.router import api_router

    _app = FastAPI(title="Agent Invest Test")
    _app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    _app.include_router(api_router, prefix="/api/v1")

    async def override_get_db():
        yield db_session

    _app.dependency_overrides[get_db] = override_get_db
    yield _app
    _app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    """Async HTTP test client."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as c:
        yield c
