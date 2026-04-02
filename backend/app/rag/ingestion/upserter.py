"""Upsert document chunks + embeddings into PostgreSQL via pgvector."""

from __future__ import annotations

import structlog
from sqlalchemy import text

from app.db.session import AsyncSessionLocal
from app.rag.ingestion.chunker import DocumentChunkData

log = structlog.get_logger(__name__)


def _format_vector(embedding: list[float]) -> str:
    """Format embedding as pgvector-compatible string: [0.1,0.2,...]"""
    return "[" + ",".join(f"{v:.6f}" for v in embedding) + "]"


async def upsert_chunks(
    chunks: list[DocumentChunkData],
    embeddings: list[list[float]],
) -> int:
    """Upsert document chunks with embeddings into document_chunks table.

    Uses INSERT ... ON CONFLICT DO UPDATE for idempotent re-ingestion.
    Returns the number of chunks upserted.
    """
    if not chunks or not embeddings or len(chunks) != len(embeddings):
        log.warning("upserter_invalid_input", chunks=len(chunks), embeddings=len(embeddings))
        return 0

    upserted = 0

    async with AsyncSessionLocal() as session:
        for chunk, embedding in zip(chunks, embeddings):
            try:
                await session.execute(
                    text("""
                        INSERT INTO document_chunks
                            (id, ticker, document_type, source_url, published_date,
                             fiscal_period, section, chunk_index, total_chunks,
                             text, entities, freshness_score, embedding)
                        VALUES
                            (:id, :ticker, :doc_type, :source_url, :pub_date,
                             :fiscal_period, :section, :chunk_index, :total_chunks,
                             :text, :entities, :freshness_score, :embedding::vector)
                        ON CONFLICT (id) DO UPDATE SET
                            text = EXCLUDED.text,
                            embedding = EXCLUDED.embedding,
                            freshness_score = EXCLUDED.freshness_score,
                            entities = EXCLUDED.entities
                    """),
                    {
                        "id": chunk.chunk_id,
                        "ticker": chunk.ticker,
                        "doc_type": chunk.document_type,
                        "source_url": chunk.source_url,
                        "pub_date": chunk.published_date,
                        "fiscal_period": chunk.metadata.get("fiscal_period"),
                        "section": chunk.metadata.get("section"),
                        "chunk_index": chunk.chunk_index,
                        "total_chunks": chunk.total_chunks,
                        "text": chunk.text,
                        "entities": chunk.metadata.get("entities"),
                        "freshness_score": _compute_freshness(chunk.published_date),
                        "embedding": _format_vector(embedding),
                    },
                )
                upserted += 1
            except Exception as e:
                log.error("upserter_chunk_error", chunk_id=chunk.chunk_id, error=str(e))

        await session.commit()

    log.info("upserter_complete", upserted=upserted, total=len(chunks))
    return upserted


def _compute_freshness(published_date) -> float:
    """Compute freshness score 0-1 (1 = today, decays over 365 days)."""
    if published_date is None:
        return 0.5  # Unknown date gets neutral score

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    try:
        if published_date.tzinfo is None:
            from datetime import timezone as tz
            published_date = published_date.replace(tzinfo=tz.utc)
        age_days = (now - published_date).days
        return max(0.0, 1.0 - (age_days / 365.0))
    except Exception:
        return 0.5
