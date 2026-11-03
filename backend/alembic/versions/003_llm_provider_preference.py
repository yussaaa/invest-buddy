"""user_preferences.llm_provider — the model provider chosen in Settings.

Nullable with no default: NULL means "use MODEL_PROVIDER", so existing rows
keep today's behaviour.

Idempotent on purpose. `app/db/migrate.py` infers how far an unstamped dev
schema has got from which *tables* exist, and this revision adds a column, so
a schema built by `create_all` after this change is stamped at 002 with the
column already present.

Revision ID: 003
Revises: 002
Create Date: 2026-11-03
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column() -> bool:
    columns = sa.inspect(op.get_bind()).get_columns("user_preferences")
    return any(c["name"] == "llm_provider" for c in columns)


def upgrade() -> None:
    if not _has_column():
        op.add_column("user_preferences", sa.Column("llm_provider", sa.String(20), nullable=True))


def downgrade() -> None:
    if _has_column():
        op.drop_column("user_preferences", "llm_provider")
