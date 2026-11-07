"""news_headlines — the market news wire.

One row per headline from Alpaca, SEC EDGAR or the Fed, filled in stages
(ingest, triage, explanation, measured reaction). Idempotent like 003 and
004, so a dev schema that `create_all` already built upgrades cleanly.

Revision ID: 005
Revises: 004
Create Date: 2026-11-06
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "news_headlines"


def _has_table() -> bool:
    return TABLE in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _has_table():
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(24), primary_key=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("source_id", sa.String(80), nullable=False),
        sa.Column("publisher", sa.String(80), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symbols", postgresql.ARRAY(sa.String(16)),
                  server_default="{}", nullable=False),
        sa.Column("category", sa.String(24), nullable=True),
        sa.Column("tags", postgresql.ARRAY(sa.String(64)),
                  server_default="{}", nullable=False),
        sa.Column("importance", sa.SmallInteger(), nullable=True),
        sa.Column("exposures", postgresql.JSONB(), nullable=True),
        sa.Column("triaged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("explanation", postgresql.JSONB(), nullable=True),
        sa.Column("reaction", postgresql.JSONB(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_news_headlines_published_at", TABLE, ["published_at"])
    # The stream's cursor: "what changed since I last looked".
    op.create_index("ix_news_headlines_updated_at", TABLE, ["updated_at"])
    # "Every headline that names NVDA" — an array containment query.
    op.create_index("ix_news_headlines_symbols", TABLE, ["symbols"], postgresql_using="gin")


def downgrade() -> None:
    if _has_table():
        op.drop_index("ix_news_headlines_symbols", table_name=TABLE)
        op.drop_index("ix_news_headlines_updated_at", table_name=TABLE)
        op.drop_index("ix_news_headlines_published_at", table_name=TABLE)
        op.drop_table(TABLE)
