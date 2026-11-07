"""Deployment-wide settings: the Settings page first, .env as the fallback.

These configure what the server does on everyone's behalf, so they are one
value per deployment, not per user. The first is the contact the SEC sees on
every EDGAR request: its fair-access policy refuses anonymous clients, and
the contact it wants is whoever runs the server — the person who can slow or
stop it — not whichever visitor happens to have the page open.

Values live in Postgres so every replica agrees, and are mirrored in-process
so the hot paths (a source's status, an outgoing request's headers) stay
synchronous. `refresh()` re-reads at most every REFRESH_SECONDS, so a value
saved on one replica reaches the one running the news loop within that.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Literal, Optional

import structlog

from app.config import get_settings

log = structlog.get_logger(__name__)

SEC_CONTACT = "sec_contact"
SEC_CONTACT_MAX = 200
REFRESH_SECONDS = 30.0

Source = Optional[Literal["settings", "env"]]

_values: dict[str, str] = {}
_loaded_at = float("-inf")
_lock = asyncio.Lock()

_EMAIL = re.compile(r"[^\s@<>]+@[^\s@<>]+\.[A-Za-z]{2,}")


async def refresh(force: bool = False) -> None:
    """Re-read stored settings. A database failure keeps the last known values."""
    global _values, _loaded_at
    if not force and time.monotonic() - _loaded_at < REFRESH_SECONDS:
        return
    async with _lock:
        if not force and time.monotonic() - _loaded_at < REFRESH_SECONDS:
            return
        try:
            from app.db.repositories_settings import all_settings
            from app.db.session import AsyncSessionLocal

            async with AsyncSessionLocal() as session:
                _values = await all_settings(session)
        except Exception as e:
            log.warning("app_settings_refresh_failed", error=str(e)[:200])
        _loaded_at = time.monotonic()


async def save(key: str, value: str) -> None:
    """Store a value ("" removes it). Raises if the database is unavailable."""
    from app.db.repositories_settings import set_setting
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        await set_setting(session, key, value)
        await session.commit()
    if value:
        _values[key] = value
    else:
        _values.pop(key, None)


def sec_contact() -> tuple[str, Source]:
    """The SEC contact in force, and where it came from."""
    stored = _values.get(SEC_CONTACT, "")
    if stored:
        return stored, "settings"
    env = get_settings().sec_user_agent.strip()
    return (env, "env") if env else ("", None)


def validate_sec_contact(raw: str) -> str:
    """Normalise a contact for use as a User-Agent header, or raise ValueError.

    It goes into an HTTP header verbatim, so line breaks are collapsed (no
    header injection) and it must be ASCII (httpx encodes headers as ASCII).
    "" is valid: it clears the stored value.
    """
    value = " ".join(raw.split())
    if not value:
        return ""
    if len(value) > SEC_CONTACT_MAX:
        raise ValueError(f"Keep it under {SEC_CONTACT_MAX} characters.")
    if not value.isascii() or not value.isprintable():
        raise ValueError("Use plain ASCII letters — it is sent as an HTTP header.")
    if not _EMAIL.search(value):
        raise ValueError(
            'Include an email address the SEC can reach, e.g. "Jane Doe jane@example.com".'
        )
    return value
