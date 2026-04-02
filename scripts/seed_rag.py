#!/usr/bin/env python3
"""Seed the pgvector store with SEC filings and news for top tickers.

Usage:
    python scripts/seed_rag.py
    python scripts/seed_rag.py --tickers AAPL MSFT GOOGL
    python scripts/seed_rag.py --ticker AAPL --filing-type 10-K

Runs the full ingestion pipeline:
  1. Load documents (SEC filings + news articles)
  2. Chunk into overlapping segments
  3. Embed with sentence-transformers (BAAI/bge-small-en-v1.5)
  4. Upsert into PostgreSQL via pgvector
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))


# Top 20 tickers to seed by default
DEFAULT_TICKERS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA",
    "META", "TSLA", "BRK-B", "JPM", "JNJ",
    "V", "UNH", "XOM", "MA", "PG",
    "HD", "CVX", "LLY", "ABBV", "MRK",
]


async def setup_db():
    """Ensure tables + pgvector extension exist."""
    from app.db.session import engine, Base
    from app.db import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    from app.rag.pgvector_setup import setup_pgvector
    await setup_pgvector(engine)
    print("  DB tables + pgvector ready")


async def seed_ticker(ticker: str) -> dict:
    """Run the full ingestion pipeline for one ticker."""
    from app.rag.ingestion.loader import load_all_documents
    from app.rag.ingestion.chunker import chunk_documents
    from app.rag.ingestion.embedder import get_embedder
    from app.rag.ingestion.upserter import upsert_chunks

    print(f"  [{ticker}] Loading documents...")
    docs = await load_all_documents(ticker)
    if not docs:
        print(f"  [{ticker}] No documents found — skipping")
        return {"ticker": ticker, "docs": 0, "chunks": 0, "upserted": 0}

    print(f"  [{ticker}] {len(docs)} documents loaded, chunking...")
    chunks = chunk_documents(docs)
    if not chunks:
        print(f"  [{ticker}] No chunks produced — skipping")
        return {"ticker": ticker, "docs": len(docs), "chunks": 0, "upserted": 0}

    print(f"  [{ticker}] {len(chunks)} chunks, embedding...")
    embedder = get_embedder()
    texts = [c.text for c in chunks]
    embeddings = await embedder.embed_texts(texts)

    print(f"  [{ticker}] Upserting to pgvector...")
    upserted = await upsert_chunks(chunks, embeddings)

    print(f"  [{ticker}] Done: {len(docs)} docs → {len(chunks)} chunks → {upserted} upserted")
    return {"ticker": ticker, "docs": len(docs), "chunks": len(chunks), "upserted": upserted}


async def main(tickers: list[str]):
    print(f"\n{'='*60}")
    print(f"Seeding RAG pipeline — {len(tickers)} tickers")
    print(f"{'='*60}\n")

    await setup_db()

    results = []
    for i, ticker in enumerate(tickers, 1):
        print(f"\n[{i}/{len(tickers)}] Processing {ticker}...")
        try:
            result = await seed_ticker(ticker)
            results.append(result)
        except Exception as e:
            print(f"  [{ticker}] ERROR: {e}")
            results.append({"ticker": ticker, "error": str(e)})

    # Summary
    total_docs = sum(r.get("docs", 0) for r in results)
    total_chunks = sum(r.get("chunks", 0) for r in results)
    total_upserted = sum(r.get("upserted", 0) for r in results)
    errors = sum(1 for r in results if "error" in r)

    print(f"\n{'='*60}")
    print(f"Seeding complete")
    print(f"  Tickers processed: {len(results)}")
    print(f"  Total documents:   {total_docs}")
    print(f"  Total chunks:      {total_chunks}")
    print(f"  Total upserted:    {total_upserted}")
    if errors:
        print(f"  Errors:            {errors}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed RAG vector store")
    parser.add_argument("--tickers", nargs="+", default=DEFAULT_TICKERS)
    args = parser.parse_args()
    asyncio.run(main(tickers=args.tickers))
