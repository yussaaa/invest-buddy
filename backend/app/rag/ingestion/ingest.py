"""Callable ingestion functions — wraps the seed_rag logic as importable async functions.

Used by:
  - The analysis endpoint (inline ingestion when use_rag=True)
  - The seed_rag.py script (bulk pre-loading)
"""

from __future__ import annotations

from pathlib import Path

import structlog
from sqlalchemy import text

from app.db.session import AsyncSessionLocal
from app.rag.ingestion.chunker import chunk_documents, DocumentChunkData
from app.rag.ingestion.embedder import get_embedder
from app.rag.ingestion.loader import RawDocument, load_all_documents
from app.rag.ingestion.upserter import upsert_chunks

log = structlog.get_logger(__name__)


async def check_ticker_indexed(ticker: str, min_chunks: int = 3) -> bool:
    """Check if a ticker already has enough indexed chunks in pgvector."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("SELECT COUNT(*) FROM document_chunks WHERE ticker = :ticker"),
            {"ticker": ticker.upper()},
        )
        count = result.scalar() or 0
    return count >= min_chunks


async def ingest_ticker(ticker: str) -> dict:
    """Run the full ingestion pipeline for a ticker.

    1. Load SEC filings + news articles
    2. Chunk into overlapping segments
    3. Embed with sentence-transformers
    4. Upsert into pgvector

    Returns: {ticker, docs, chunks, upserted}
    """
    log.info("ingest_ticker_start", ticker=ticker)

    docs = await load_all_documents(ticker)
    if not docs:
        log.info("ingest_ticker_no_docs", ticker=ticker)
        return {"ticker": ticker, "docs": 0, "chunks": 0, "upserted": 0}

    chunks = chunk_documents(docs)
    if not chunks:
        return {"ticker": ticker, "docs": len(docs), "chunks": 0, "upserted": 0}

    embedder = get_embedder()
    texts = [c.text for c in chunks]
    embeddings = await embedder.embed_texts(texts)

    upserted = await upsert_chunks(chunks, embeddings)

    log.info(
        "ingest_ticker_complete",
        ticker=ticker,
        docs=len(docs),
        chunks=len(chunks),
        upserted=upserted,
    )
    return {"ticker": ticker, "docs": len(docs), "chunks": len(chunks), "upserted": upserted}


async def ingest_custom_files(
    ticker: str,
    file_paths: list[str],
    user_id: str = "anonymous",
) -> dict:
    """Ingest user-uploaded files into pgvector.

    Reads each file (TXT, PDF, CSV), converts to RawDocument,
    then runs through the standard chunk → embed → upsert pipeline.

    Returns: {ticker, files, chunks, upserted}
    """
    log.info("ingest_custom_start", ticker=ticker, files=len(file_paths))

    docs: list[RawDocument] = []
    for fpath in file_paths:
        p = Path(fpath)
        if not p.exists():
            log.warning("ingest_custom_file_not_found", path=fpath)
            continue

        try:
            # Read file content
            if p.suffix.lower() == ".pdf":
                text_content = _read_pdf(p)
            else:
                text_content = p.read_text(encoding="utf-8", errors="ignore")

            if len(text_content.strip()) < 20:
                log.warning("ingest_custom_file_too_short", path=fpath)
                continue

            docs.append(RawDocument(
                ticker=ticker.upper(),
                document_type="user_upload",
                text=text_content,
                source_url=f"upload://{user_id}/{p.name}",
                published_date=None,
                metadata={"filename": p.name, "user_id": user_id},
            ))
        except Exception as e:
            log.error("ingest_custom_file_error", path=fpath, error=str(e))

    if not docs:
        return {"ticker": ticker, "files": 0, "chunks": 0, "upserted": 0}

    chunks = chunk_documents(docs)
    embedder = get_embedder()
    texts = [c.text for c in chunks]
    embeddings = await embedder.embed_texts(texts)
    upserted = await upsert_chunks(chunks, embeddings)

    log.info(
        "ingest_custom_complete",
        ticker=ticker,
        files=len(docs),
        chunks=len(chunks),
        upserted=upserted,
    )
    return {"ticker": ticker, "files": len(docs), "chunks": len(chunks), "upserted": upserted}


def _read_pdf(path: Path) -> str:
    """Extract text from a PDF file. Falls back to empty string on error."""
    try:
        import subprocess
        # Use pdftotext if available (better quality), else fall back
        result = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fallback: try PyPDF2 or similar
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        return text
    except ImportError:
        pass

    log.warning("pdf_read_failed", path=str(path))
    return ""
