"""FastAPI application entry point."""

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import make_asgi_app

from app.config import get_settings
from app.api.v1.router import api_router

log = structlog.get_logger(__name__)


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

    log.info("startup_complete")
    yield
    log.info("shutdown")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Agent Invest API",
        description="Multi-Agent Stock Investment Analysis Platform",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS — allow the Vite frontend
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount Prometheus metrics at /metrics
    metrics_app = make_asgi_app()
    app.mount("/metrics", metrics_app)

    # Register API routes
    app.include_router(api_router, prefix="/api/v1")

    return app


app = create_app()
