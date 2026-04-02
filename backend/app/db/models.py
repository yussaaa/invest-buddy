"""SQLAlchemy ORM models — 5 tables for Phase 2 persistence.

Design decisions:
- `final_report` and `agent_results` stored as JSONB — queryable without
  normalizing the nested agent data into separate tables
- PostgreSQL arrays for `tickers`, `preferred_sectors`, etc.
- UUIDs as primary keys for analysis_runs and watchlists
- `users` table is lightweight — just ensures FK integrity, no auth
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    ARRAY,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
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
