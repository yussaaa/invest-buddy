"""FastAPI application entry point."""

from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from fastapi.responses import Response

from app.config import get_settings
from app.api.v1.router import api_router

log = structlog.get_logger(__name__)


def _stamp_alembic_head(connection) -> None:
    """Mark a create_all-built schema as being at the latest revision.

    Runs inside `connection.run_sync`, so Alembic's synchronous API is fine
    here. No-op if the database is already stamped.
    """
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    if MigrationContext.configure(connection).get_current_revision() is not None:
        return

    root = Path(__file__).resolve().parent.parent  # backend/
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    head = ScriptDirectory.from_config(config).get_current_head()
    if head:
        MigrationContext.configure(connection).stamp(
            ScriptDirectory.from_config(config), head
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown logic."""
    settings = get_settings()
    log.info("startup", env=settings.app_env, provider=settings.model_provider)

    # Warm up the tool registry
    from app.tools.registry import ToolRegistry
    ToolRegistry.get()  # initialises and registers all tools

    # Warm up the LangGraph (compiles the graph)
    from app.agents.orchestrator.graph import get_graph
    get_graph()

    # In dev mode, auto-create tables (production should use alembic)
    if settings.app_env == "development":
        from app.db.session import Base, engine
        from app.db import models  # noqa: F401 — register ORM models with Base

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # create_all builds the schema without telling Alembic, so a later
            # `alembic upgrade head` would try to re-create these tables and
            # fail on "relation already exists". Stamping the current head
            # here keeps the two paths from colliding on the same database.
            await conn.run_sync(_stamp_alembic_head)
        log.info("dev_tables_created")

        # Enable pgvector extension + create embedding column + HNSW index
        from app.rag.pgvector_setup import setup_pgvector
        await setup_pgvector(engine)
        log.info("pgvector_setup_complete")

    log.info("startup_complete")
    yield
    log.info("shutdown")


def create_app() -> FastAPI:
    settings = get_settings()

    application = FastAPI(
        title="Agent Invest API",
        description="Multi-Agent Stock Investment Analysis Platform",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS — allow the Vite frontend
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Prometheus metrics endpoint
    @application.get("/metrics")
    async def prometheus_metrics():
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    # Register API routes
    application.include_router(api_router, prefix="/api/v1")

    return application


# Import metrics to register Prometheus collectors before app creation
from app.observability.metrics import (  # noqa: E402, F401
    ANALYSIS_RUNS_TOTAL, RAGAS_FAITHFULNESS, TOOL_CALLS_TOTAL,
)

app = create_app()
