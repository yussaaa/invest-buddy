"""pgvector setup — enable the extension and create the embedding column.

Run once on startup (dev mode) or via migration (production).
Uses raw SQL because SQLAlchemy's pgvector integration requires
the extension to be enabled first.
"""

from __future__ import annotations

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import get_settings

log = structlog.get_logger(__name__)

# Dimension map for common models
MODEL_DIMENSIONS = {
    "BAAI/bge-small-en-v1.5": 384,
    "BAAI/bge-base-en-v1.5": 768,
    "all-MiniLM-L6-v2": 384,
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
}


def get_embedding_dimension() -> int:
    """Return the vector dimension for the configured embedding model."""
    settings = get_settings()
    dim = MODEL_DIMENSIONS.get(settings.embedding_model)
    if dim is None:
        log.warning("unknown_embedding_model_dimension", model=settings.embedding_model, default=384)
        return 384
    return dim


async def setup_pgvector(engine: AsyncEngine) -> None:
    """Enable pgvector extension and add embedding column to document_chunks."""
    dim = get_embedding_dimension()

    async with engine.begin() as conn:
        # Enable pgvector extension
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        log.info("pgvector_extension_enabled")

        # Add embedding column if it doesn't exist
        # (can't do this via ORM easily — pgvector column needs explicit dimension)
        await conn.execute(text(f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'document_chunks' AND column_name = 'embedding'
                ) THEN
                    ALTER TABLE document_chunks ADD COLUMN embedding vector({dim});
                END IF;
            END $$;
        """))

        # Create HNSW index for fast approximate nearest neighbor search
        await conn.execute(text(f"""
            CREATE INDEX IF NOT EXISTS idx_document_chunks_embedding
            ON document_chunks
            USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64);
        """))

        # Create composite index for filtered vector search
        # (ticker filter + vector search is the primary query pattern)
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_document_chunks_ticker_date
            ON document_chunks (ticker, published_date DESC NULLS LAST);
        """))

        log.info("pgvector_setup_complete", dimension=dim)
