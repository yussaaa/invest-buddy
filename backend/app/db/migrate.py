"""Migration entrypoint that tolerates a schema built by `create_all`.

Dev startup creates tables directly from the ORM without recording a revision,
so a database can hold the right tables while Alembic believes it is empty —
`alembic upgrade head` then dies trying to re-create them. That is not a
hypothetical: it is the state of any environment that ran the app in dev before
migrations existed.

Run as `python -m app.db.migrate`:

  no tables            -> upgrade from scratch
  tables, no revision  -> stamp to head, then upgrade
  tables and revision  -> upgrade
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import structlog
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings

log = structlog.get_logger(__name__)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent

# Present in the first migration, so its existence means the schema predates
# whatever revision Alembic thinks we are on.
SENTINEL_TABLE = "users"

# Tables each revision introduces, oldest first. Used to work out how far an
# unstamped schema has actually progressed: stamping straight to head would
# mark later migrations as applied when their DDL never ran, and outside dev
# (where create_all quietly fills the gap) those tables would simply be missing.
#
# Add an entry whenever a migration creates tables.
REVISION_TABLES: list[tuple[str, set[str]]] = [
    ("001", {"users", "analysis_runs", "watchlists", "user_preferences", "feedback"}),
    ("002", {"daily_bars", "bar_coverage"}),
]


def _effective_revision(tables: set[str]) -> str | None:
    """The newest revision whose tables are all already present."""
    reached: str | None = None
    for revision, created in REVISION_TABLES:
        if created <= tables:
            reached = revision
        else:
            break
    return reached


async def _existing_tables() -> set[str]:
    """Table names currently in the database.

    Uses the async driver the project actually installs — stripping `+asyncpg`
    to get a sync URL would send SQLAlchemy looking for psycopg2, which is not
    a dependency here.
    """
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect() as conn:
            return set(await conn.run_sync(lambda c: inspect(c).get_table_names()))
    finally:
        await engine.dispose()


def _config() -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url)
    return config


def main() -> int:
    tables = asyncio.run(_existing_tables())

    config = _config()
    has_schema = SENTINEL_TABLE in tables
    has_revision = "alembic_version" in tables

    if has_schema and not has_revision:
        reached = _effective_revision(tables)
        if reached is None:
            # Tables exist but not even the first migration's set is complete —
            # too ambiguous to guess at. Say so rather than corrupt the history.
            log.error("migrate_unrecognised_schema", tables=sorted(tables))
            return 1
        log.info("migrate_stamping_legacy_schema", revision=reached, tables=len(tables))
        command.stamp(config, reached)

    log.info("migrate_upgrading", from_scratch=not has_schema)
    command.upgrade(config, "head")
    log.info("migrate_complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
