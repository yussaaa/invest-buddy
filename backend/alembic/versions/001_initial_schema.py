"""Initial schema — 5 tables: users, analysis_runs, watchlists,
user_preferences, feedback.

Revision ID: 001
Revises: (none — first migration)
Create Date: 2026-04-02
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- users ---
    op.create_table(
        "users",
        sa.Column("id", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_active",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # --- analysis_runs ---
    op.create_table(
        "analysis_runs",
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("ticker", sa.String(10), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("final_report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("agent_results", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("guardrail_flags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("hallucination_score", sa.Float(), nullable=True),
        sa.Column("latency_breakdown", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("required_agents", postgresql.ARRAY(sa.String()), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index("ix_analysis_runs_user_id", "analysis_runs", ["user_id"])
    op.create_index("ix_analysis_runs_ticker", "analysis_runs", ["ticker"])
    op.create_index("ix_analysis_runs_status", "analysis_runs", ["status"])
    op.create_index("ix_analysis_runs_created_at", "analysis_runs", ["created_at"])

    # --- watchlists ---
    op.create_table(
        "watchlists",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("tickers", postgresql.ARRAY(sa.String(10)), nullable=True),
        sa.Column("notes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_watchlists_user_id", "watchlists", ["user_id"])

    # --- user_preferences ---
    op.create_table(
        "user_preferences",
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column(
            "risk_tolerance", sa.String(20), nullable=False, server_default="moderate"
        ),
        sa.Column(
            "investment_horizon", sa.String(20), nullable=False, server_default="medium"
        ),
        sa.Column(
            "analysis_depth", sa.String(20), nullable=False, server_default="standard"
        ),
        sa.Column("preferred_sectors", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("preferred_metrics", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("recent_tickers", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id"),
    )

    # --- feedback ---
    op.create_table(
        "feedback",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("score", sa.SmallInteger(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_runs.run_id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_feedback_run_id", "feedback", ["run_id"])
    op.create_index("ix_feedback_user_id", "feedback", ["user_id"])
    op.create_index("ix_feedback_created_at", "feedback", ["created_at"])


def downgrade() -> None:
    op.drop_table("feedback")
    op.drop_table("user_preferences")
    op.drop_table("watchlists")
    op.drop_table("analysis_runs")
    op.drop_table("users")
