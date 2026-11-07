"""Repository for deployment-wide settings (app_settings).

Same conventions as the other repositories: module-level async functions,
session first, flush never commit — the caller owns the transaction.
"""

from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppSetting


async def all_settings(session: AsyncSession) -> dict[str, str]:
    rows = (await session.execute(select(AppSetting.key, AppSetting.value))).all()
    return {key: value for key, value in rows}


async def set_setting(session: AsyncSession, key: str, value: str | None) -> None:
    """Store a value; None or "" removes it, so the .env fallback applies again."""
    if not value:
        await session.execute(delete(AppSetting).where(AppSetting.key == key))
    else:
        stmt = pg_insert(AppSetting).values(key=key, value=value)
        await session.execute(stmt.on_conflict_do_update(
            index_elements=["key"], set_={"value": value, "updated_at": func.now()},
        ))
    await session.flush()
