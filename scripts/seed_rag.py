#!/usr/bin/env python3
"""Seed the Qdrant vector store with SEC filings and news for top tickers.

Usage:
    python scripts/seed_rag.py
    python scripts/seed_rag.py --tickers AAPL MSFT GOOGL
    python scripts/seed_rag.py --ticker AAPL --filing-type 10-K

This is the Phase 3 RAG ingestion script. Run once to populate Qdrant,
then the RAG pipeline will retrieve from it during analysis runs.
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


async def seed_ticker(ticker: str, filing_type: str = "10-K"):
    """Download filings + news for a ticker and upsert into Qdrant."""
    print(f"  Seeding {ticker}...")

    from app.tools.news.sec_edgar_tool import get_sec_filings
    from app.tools.news.newsapi_tool import get_recent_news

    # Download SEC filings
    filings = await get_sec_filings(ticker, filing_type=filing_type, limit=2)
    filing_count = len(filings.get("filings", []))
    print(f"    {ticker}: {filing_count} {filing_type} filings downloaded")

    # Download recent news
    news = await get_recent_news(ticker, days=30, max_results=20)
    article_count = len(news.get("articles", []))
    print(f"    {ticker}: {article_count} news articles fetched")

    # TODO (Phase 3): chunk + embed + upsert to Qdrant
    # from app.rag.ingestion.embedder import embed_and_upsert
    # await embed_and_upsert(ticker, filings, news)

    return {"ticker": ticker, "filings": filing_count, "articles": article_count}


async def main(tickers: list[str], filing_type: str):
    print(f"\n{'='*60}")
    print(f"Seeding RAG vector store — {len(tickers)} tickers")
    print(f"Filing type: {filing_type}")
    print(f"{'='*60}\n")

    results = []
    for ticker in tickers:
        try:
            result = await seed_ticker(ticker, filing_type)
            results.append(result)
        except Exception as e:
            print(f"  ERROR seeding {ticker}: {e}")

    print(f"\n{'='*60}")
    print(f"Seeding complete — {len(results)}/{len(tickers)} tickers processed")
    total_filings = sum(r["filings"] for r in results)
    total_articles = sum(r["articles"] for r in results)
    print(f"  Total filings: {total_filings}")
    print(f"  Total articles: {total_articles}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed RAG vector store")
    parser.add_argument("--tickers", nargs="+", default=DEFAULT_TICKERS)
    parser.add_argument("--filing-type", default="10-K", choices=["10-K", "10-Q", "8-K"])
    args = parser.parse_args()

    asyncio.run(main(tickers=args.tickers, filing_type=args.filing_type))
