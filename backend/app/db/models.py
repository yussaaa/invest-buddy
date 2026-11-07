"""SQLAlchemy ORM models — persistence, RAG and the market data store.

Design decisions:
- `final_report` and `agent_results` stored as JSONB — queryable without
  normalizing the nested agent data into separate tables
- PostgreSQL arrays for `tickers`, `preferred_sectors`, etc.
- UUIDs as primary keys for analysis_runs and watchlists
- `users` table is lightweight — just ensures FK integrity, no auth
- `document_chunks` uses pgvector for embedding storage — no separate vector DB
- `daily_bars` is a local store of settled price history, so charts and
  indicators stop depending on the upstream provider being reachable
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY as PG_ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_active: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    analysis_runs: Mapped[list["AnalysisRun"]] = relationship(back_populates="user")
    watchlists: Mapped[list["Watchlist"]] = relationship(back_populates="user")
    preferences: Mapped["UserPreferencesRow | None"] = relationship(
        back_populates="user", uselist=False
    )
    feedback_entries: Mapped[list["Feedback"]] = relationship(back_populates="user")


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"

    run_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("users.id"), nullable=False, index=True
    )
    ticker: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="running", index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now()
    )
    error: Mapped[str | None] = mapped_column(Text)

    # Result data (stored as JSONB for flexible querying)
    final_report: Mapped[dict | None] = mapped_column(JSONB)
    agent_results: Mapped[dict | None] = mapped_column(JSONB)
    guardrail_flags: Mapped[list | None] = mapped_column(JSONB)
    hallucination_score: Mapped[float | None] = mapped_column(Float)
    latency_breakdown: Mapped[dict | None] = mapped_column(JSONB)
    required_agents: Mapped[list | None] = mapped_column(ARRAY(String))

    # Relationships
    user: Mapped["User"] = relationship(back_populates="analysis_runs")
    feedback_entries: Mapped[list["Feedback"]] = relationship(back_populates="analysis_run")

    def to_dict(self) -> dict:
        """Serialize for API response — matches the Phase 1 in-memory format."""
        return {
            "run_id": self.run_id,
            "user_id": self.user_id,
            "ticker": self.ticker,
            "query": self.query,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "error": self.error,
            "result": {
                "final_report": self.final_report,
                "agent_results": self.agent_results,
                "guardrail_flags": self.guardrail_flags,
                "hallucination_score": self.hallucination_score,
                "latency_breakdown": self.latency_breakdown,
                "required_agents": self.required_agents,
            } if self.status == "completed" else None,
        }


class Watchlist(Base):
    __tablename__ = "watchlists"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("users.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    tickers: Mapped[list | None] = mapped_column(ARRAY(String(10)))
    notes: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    user: Mapped["User"] = relationship(back_populates="watchlists")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "name": self.name,
            "tickers": self.tickers or [],
            "notes": self.notes or {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class UserPreferencesRow(Base):
    __tablename__ = "user_preferences"

    user_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("users.id"), primary_key=True
    )
    risk_tolerance: Mapped[str] = mapped_column(
        String(20), nullable=False, default="moderate"
    )
    investment_horizon: Mapped[str] = mapped_column(
        String(20), nullable=False, default="medium"
    )
    analysis_depth: Mapped[str] = mapped_column(
        String(20), nullable=False, default="standard"
    )
    preferred_sectors: Mapped[list | None] = mapped_column(ARRAY(String))
    preferred_metrics: Mapped[list | None] = mapped_column(ARRAY(String))
    recent_tickers: Mapped[list | None] = mapped_column(ARRAY(String))
    # Model provider chosen on the Settings page; empty or NULL means the
    # server default (MODEL_PROVIDER). Credentials still come from .env.
    llm_provider: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    user: Mapped["User"] = relationship(back_populates="preferences")

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "risk_tolerance": self.risk_tolerance,
            "investment_horizon": self.investment_horizon,
            "analysis_depth": self.analysis_depth,
            "preferred_sectors": self.preferred_sectors or [],
            "preferred_metrics": self.preferred_metrics or [],
            "recent_tickers": self.recent_tickers or [],
            "llm_provider": self.llm_provider or None,
        }


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("analysis_runs.run_id"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("users.id"), nullable=False, index=True
    )
    score: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # -1 or 1
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    # Relationships
    analysis_run: Mapped["AnalysisRun"] = relationship(back_populates="feedback_entries")
    user: Mapped["User"] = relationship(back_populates="feedback_entries")


# ── RAG: Document Chunks (pgvector) ─────────────────────────────────────────


class DocumentChunk(Base):
    """Chunked document with pgvector embedding for RAG retrieval.

    Uses pgvector's vector type for efficient similarity search directly
    inside PostgreSQL — no separate vector database needed.
    """
    __tablename__ = "document_chunks"

    id: Mapped[str] = mapped_column(
        String(255), primary_key=True  # format: {ticker}_{doc_type}_{chunk_idx}
    )
    ticker: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    document_type: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True  # sec_filing, news, earnings_call
    )
    source_url: Mapped[str | None] = mapped_column(Text)
    published_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    fiscal_period: Mapped[str | None] = mapped_column(String(20))  # e.g. Q4-2024
    section: Mapped[str | None] = mapped_column(String(100))  # risk_factors, financials
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    total_chunks: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    entities: Mapped[list | None] = mapped_column(ARRAY(String))  # extracted entity names
    freshness_score: Mapped[float | None] = mapped_column(Float)  # 0-1, newer = higher

    # pgvector embedding column — added via raw SQL in migration
    # (SQLAlchemy pgvector integration handles this)
    # embedding: Vector(384)  — dimension depends on model

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "ticker": self.ticker,
            "document_type": self.document_type,
            "source_url": self.source_url,
            "published_date": self.published_date.isoformat() if self.published_date else None,
            "fiscal_period": self.fiscal_period,
            "section": self.section,
            "chunk_index": self.chunk_index,
            "total_chunks": self.total_chunks,
            "text": self.text[:200] + "..." if len(self.text) > 200 else self.text,
            "entities": self.entities or [],
            "freshness_score": self.freshness_score,
        }


class TickerEntity(Base):
    """Entity relationships extracted from documents for the entity graph."""
    __tablename__ = "ticker_entities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)  # company, person, product
    entity_name: Mapped[str] = mapped_column(String(200), nullable=False)
    relationship_type: Mapped[str | None] = mapped_column(String(50))  # ceo_of, competitor_of
    related_entity: Mapped[str | None] = mapped_column(String(200))
    source_chunk_id: Mapped[str | None] = mapped_column(String(255))
    confidence: Mapped[float | None] = mapped_column(Float)
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DailyBar(Base):
    """One settled end-of-day bar.

    Rows here are immutable in normal operation — a session's close does not
    change once the market shuts. That is what makes this a store rather than a
    cache: there is no TTL, and a five-year indicator window is one query.

    Today's in-progress session is deliberately never written; a partial bar
    persisted here would be served to every replica until someone deleted it.

    Both raw and adjusted closes are kept because the two consumers disagree:
    the chart wants raw prices, while the indicators run on the split- and
    dividend-adjusted series. Storing one silently changes the other.
    """
    __tablename__ = "daily_bars"

    # Wider than the String(10) used elsewhere — FX and index symbols such as
    # DX-Y.NYB run long, and the extra bytes cost nothing.
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    bar_date: Mapped[date] = mapped_column(Date, primary_key=True)

    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    adj_close: Mapped[float | None] = mapped_column(Float)
    # Crypto and index volumes exceed a 32-bit int — BTC-USD trades ~4e10/day.
    volume: Mapped[int | None] = mapped_column(BigInteger)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # The composite PK covers "this symbol over a date range"; the standalone
    # date index covers breadth's "every symbol on one date".
    __table_args__ = (Index("ix_daily_bars_bar_date", "bar_date"),)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "date": self.bar_date.isoformat() if self.bar_date else None,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "adj_close": self.adj_close,
            "volume": self.volume,
        }


class BarCoverage(Base):
    """What we can honestly claim to hold for a symbol.

    MIN/MAX over daily_bars cannot answer this: a missing date is
    indistinguishable between "market holiday" and "never fetched", and a
    request for full history would silently return a truncated series when we
    only ever backfilled two years.
    """
    __tablename__ = "bar_coverage"

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    first_bar_date: Mapped[date | None] = mapped_column(Date)
    last_bar_date: Mapped[date | None] = mapped_column(Date)
    bar_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # How far back we asked for, so a MAX request knows whether we can serve it.
    requested_span: Mapped[str] = mapped_column(String(8), default="2y", nullable=False)
    last_refresh_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_bar_coverage_last_refresh", "last_refresh_at"),)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "first_bar_date": self.first_bar_date.isoformat() if self.first_bar_date else None,
            "last_bar_date": self.last_bar_date.isoformat() if self.last_bar_date else None,
            "bar_count": self.bar_count,
            "requested_span": self.requested_span,
            "last_refresh_at": (
                self.last_refresh_at.isoformat() if self.last_refresh_at else None
            ),
            "last_error": self.last_error,
        }


class SocialMentionSnapshot(Base):
    """One hourly sample of a ticker's place on a social mention board.

    The baseline a mention z-score needs. ApeWisdom reports "now" and "24
    hours ago" and keeps nothing older, so the history has to be ours — and in
    Postgres rather than Redis, because Redis here is a cache that may evict,
    and silently losing the baseline would make every ticker look unusual.

    Hour-truncated `bucket` in the key makes the hourly job idempotent: a
    second run inside the same hour is a no-op, not a duplicate.
    """
    __tablename__ = "social_mention_snapshots"

    source: Mapped[str] = mapped_column(String(16), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    bucket: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)

    rank: Mapped[int | None] = mapped_column(Integer)
    mentions: Mapped[int] = mapped_column(Integer, nullable=False)
    upvotes: Mapped[int | None] = mapped_column(Integer)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_social_mention_snapshots_bucket", "bucket"),)


class NewsHeadline(Base):
    """One market headline on the news wire, from any source.

    `id` is a hash of (source, source_id), so a re-poll or a websocket replay
    of the same item is an idempotent no-op. Alpaca, EDGAR and the Fed are
    each other's strangers: the same story from two sources is two rows, and
    any merging is the triage step's business, not the key's.

    Written in stages, each of which stamps `updated_at` — that column is the
    stream's cursor, so the page learns about a new headline and, later, its
    triage, explanation and measured reaction through one mechanism:

    1. ingest (services/news/runner.py) — everything up to `tags`
    2. triage — `importance`, `exposures`, `triaged_at`
    3. explain — `explanation`
    4. reaction — `reaction`, filled once each horizon has passed
    """
    __tablename__ = "news_headlines"

    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    source_id: Mapped[str] = mapped_column(String(80), nullable=False)
    publisher: Mapped[str | None] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # The dialect's ARRAY, not the generic one: only it has `.contains()`,
    # which the "headlines naming NVDA" query needs.
    symbols: Mapped[list] = mapped_column(PG_ARRAY(String(16)), nullable=False, server_default="{}")
    category: Mapped[str | None] = mapped_column(String(24))
    # Source-specific labels — 8-K item numbers, the Fed's release category.
    tags: Mapped[list] = mapped_column(PG_ARRAY(String(64)), nullable=False, server_default="{}")

    # {symbol: {"sector", "industry"} | null}, filled just after ingest
    # (services/news/classify.py). Null until then; a symbol with no profile
    # maps to null, so the row is not looked up again.
    symbol_info: Mapped[dict | None] = mapped_column(JSONB)
    importance: Mapped[int | None] = mapped_column(SmallInteger)
    exposures: Mapped[list | None] = mapped_column(JSONB)
    triaged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    explanation: Mapped[dict | None] = mapped_column(JSONB)
    reaction: Mapped[dict | None] = mapped_column(JSONB)

    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_news_headlines_published_at", "published_at"),
        Index("ix_news_headlines_updated_at", "updated_at"),
        Index("ix_news_headlines_symbols", "symbols", postgresql_using="gin"),
    )


class AppSetting(Base):
    """A setting for the whole deployment, edited from the Settings page.

    Not a user preference: these configure what the *server* does on
    everyone's behalf — the contact the SEC sees on every EDGAR request, for
    one. A value here takes precedence over the matching .env setting, which
    stays as the fallback for a deployment configured entirely from the file.
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
