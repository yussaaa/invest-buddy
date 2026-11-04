"""social_mention_snapshots — hourly samples of the Reddit mention board.

The history behind the Signals page's attention z-score. Idempotent, like
003, so a dev schema that `create_all` already built upgrades cleanly.

Revision ID: 004
Revises: 003
Create Date: 2026-11-03
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "social_mention_snapshots"


def _has_table() -> bool:
    return TABLE in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _has_table():
        return
    op.create_table(
        TABLE,
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("bucket", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("mentions", sa.Integer(), nullable=False),
        sa.Column("upvotes", sa.Integer(), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("source", "symbol", "bucket"),
    )
    # The PK serves "one symbol over time"; this serves the prune.
    op.create_index("ix_social_mention_snapshots_bucket", TABLE, ["bucket"])


def downgrade() -> None:
    if _has_table():
        op.drop_index("ix_social_mention_snapshots_bucket", table_name=TABLE)
        op.drop_table(TABLE)
