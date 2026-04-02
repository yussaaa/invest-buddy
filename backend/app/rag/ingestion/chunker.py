"""Document chunker — sliding window with sentence boundary respect."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import structlog

from app.config import get_settings
from app.rag.ingestion.loader import RawDocument

log = structlog.get_logger(__name__)


@dataclass
class DocumentChunkData:
    chunk_id: str              # deterministic: {ticker}_{type}_{hash}_{idx}
    ticker: str
    document_type: str
    text: str
    chunk_index: int
    total_chunks: int
    source_url: Optional[str] = None
    published_date: Optional[datetime] = None
    metadata: dict = field(default_factory=dict)


def _make_chunk_id(ticker: str, doc_type: str, text: str, idx: int) -> str:
    """Deterministic chunk ID for idempotent upserts."""
    content_hash = hashlib.md5(text[:200].encode()).hexdigest()[:8]
    return f"{ticker}_{doc_type}_{content_hash}_{idx}"


def _find_sentence_boundary(text: str, pos: int, window: int = 100) -> int:
    """Find the nearest sentence boundary near `pos` (search backwards within window)."""
    if pos >= len(text):
        return len(text)

    search_start = max(0, pos - window)
    search_region = text[search_start:pos]

    # Look for sentence endings: ". ", ".\n", "? ", "! "
    for sep in [". ", ".\n", "? ", "! ", "\n\n", "\n"]:
        last_sep = search_region.rfind(sep)
        if last_sep != -1:
            return search_start + last_sep + len(sep)

    return pos  # No boundary found — split at exact position


def chunk_document(
    doc: RawDocument,
    chunk_size: int | None = None,
    overlap: int | None = None,
) -> list[DocumentChunkData]:
    """Split a document into overlapping chunks respecting sentence boundaries."""
    settings = get_settings()
    chunk_size = chunk_size or settings.rag_chunk_size
    overlap = overlap or settings.rag_chunk_overlap

    text = doc.text.strip()
    if not text:
        return []

    # For short documents, return as single chunk
    if len(text) <= chunk_size:
        return [DocumentChunkData(
            chunk_id=_make_chunk_id(doc.ticker, doc.document_type, text, 0),
            ticker=doc.ticker,
            document_type=doc.document_type,
            text=text,
            chunk_index=0,
            total_chunks=1,
            source_url=doc.source_url,
            published_date=doc.published_date,
            metadata=doc.metadata,
        )]

    chunks: list[DocumentChunkData] = []
    start = 0

    while start < len(text):
        end = start + chunk_size

        # Find sentence boundary near the end position
        if end < len(text):
            end = _find_sentence_boundary(text, end)

        chunk_text = text[start:end].strip()
        if chunk_text:
            chunks.append(DocumentChunkData(
                chunk_id=_make_chunk_id(doc.ticker, doc.document_type, chunk_text, len(chunks)),
                ticker=doc.ticker,
                document_type=doc.document_type,
                text=chunk_text,
                chunk_index=len(chunks),
                total_chunks=0,  # filled after loop
                source_url=doc.source_url,
                published_date=doc.published_date,
                metadata=doc.metadata,
            ))

        # Move start forward (with overlap)
        start = end - overlap
        if start <= 0 and end > 0:
            start = end  # Prevent infinite loop

    # Update total_chunks
    for c in chunks:
        c.total_chunks = len(chunks)

    return chunks


def chunk_documents(docs: list[RawDocument]) -> list[DocumentChunkData]:
    """Chunk a batch of documents."""
    all_chunks: list[DocumentChunkData] = []
    for doc in docs:
        all_chunks.extend(chunk_document(doc))
    log.info("chunker_complete", docs=len(docs), chunks=len(all_chunks))
    return all_chunks
