"""Document loader — fetches raw documents from SEC EDGAR and NewsAPI."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import structlog

log = structlog.get_logger(__name__)


@dataclass
class RawDocument:
    ticker: str
    document_type: str         # "sec_filing" | "news"
    text: str
    source_url: Optional[str] = None
    published_date: Optional[datetime] = None
    metadata: dict = field(default_factory=dict)


async def load_sec_filings(
    ticker: str, filing_type: str = "10-K", limit: int = 3
) -> list[RawDocument]:
    """Load SEC filings via the existing tool and convert to RawDocuments."""
    from app.tools.news.sec_edgar_tool import get_sec_filings

    result = await get_sec_filings(ticker, filing_type=filing_type, limit=limit)
    docs = []
    for filing in result.get("filings", []):
        text = filing.get("excerpt", "")
        if not text.strip():
            continue
        docs.append(RawDocument(
            ticker=ticker.upper(),
            document_type="sec_filing",
            text=text,
            source_url=filing.get("url"),
            published_date=None,
            metadata={
                "filing_type": filing_type,
                "accession_number": filing.get("accession_number"),
            },
        ))
    log.info("loader_sec_filings", ticker=ticker, count=len(docs))
    return docs


async def load_news_articles(
    ticker: str, days: int = 30, max_results: int = 20
) -> list[RawDocument]:
    """Load news articles via the existing tool and convert to RawDocuments."""
    from app.tools.news.newsapi_tool import get_recent_news

    result = await get_recent_news(ticker, days=days, max_results=max_results)
    docs = []
    for article in result.get("articles", []):
        text = f"{article.get('title', '')}\n\n{article.get('description', '')}"
        if len(text.strip()) < 20:
            continue

        pub_date = None
        if article.get("published_at"):
            try:
                pub_date = datetime.fromisoformat(article["published_at"].replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass

        docs.append(RawDocument(
            ticker=ticker.upper(),
            document_type="news",
            text=text,
            source_url=article.get("url"),
            published_date=pub_date,
            metadata={"source": article.get("source", "")},
        ))
    log.info("loader_news", ticker=ticker, count=len(docs))
    return docs


async def load_all_documents(ticker: str) -> list[RawDocument]:
    """Load all available documents for a ticker (SEC + news in parallel)."""
    filings, news = await asyncio.gather(
        load_sec_filings(ticker),
        load_news_articles(ticker),
        return_exceptions=True,
    )
    docs: list[RawDocument] = []
    if isinstance(filings, list):
        docs.extend(filings)
    else:
        log.error("loader_sec_error", ticker=ticker, error=str(filings))
    if isinstance(news, list):
        docs.extend(news)
    else:
        log.error("loader_news_error", ticker=ticker, error=str(news))

    log.info("loader_complete", ticker=ticker, total_docs=len(docs))
    return docs
