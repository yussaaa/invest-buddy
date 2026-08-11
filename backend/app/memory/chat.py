"""Conversation history for the chat agent, in Redis.

Built on `memory/session.py`, which has had `save_session`/`get_session` since
the persistence phase and no caller until now.

Redis rather than Postgres deliberately. Chat about a live chart is inherently
of the moment — the signals it is discussing expire in minutes, and a
transcript outliving them is of no use to anyone. The existing two-hour session
expiry is the right lifetime, and it costs no table, no migration, and no entry
in the REVISION_TABLES map that `db/migrate.py` needs kept in step.

Every function fails open. A conversation that loses its history answers the
next question without it; one that raises answers nothing.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import structlog

from app.memory.session import SESSION_TTL, get_session, save_session

log = structlog.get_logger(__name__)

# Turns kept in Redis. Roughly a dozen exchanges, well past the point where a
# chart conversation has moved on to a different ticker.
MAX_STORED_MESSAGES = 24


def new_conversation_id() -> str:
    return uuid.uuid4().hex


def _key(conversation_id: str) -> str:
    return f"chat:{conversation_id}"


async def load(conversation_id: Optional[str]) -> dict:
    """The stored conversation, or an empty one."""
    if not conversation_id:
        return {}
    return await get_session(_key(conversation_id)) or {}


async def history_for_model(
    conversation_id: Optional[str], limit: int
) -> list[dict[str, Any]]:
    """Prior turns as OpenAI-shaped messages, oldest first.

    Only user and assistant text is replayed. Tool calls and their results are
    dropped: they are bulky, they reference readings that have since gone stale,
    and a tool_call without its matching result — which trimming can easily
    produce — is a malformed sequence that some providers reject outright.
    """
    stored = await load(conversation_id)
    messages = stored.get("messages") or []

    replayable = [
        {"role": m["role"], "content": m["content"]}
        for m in messages
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]
    return replayable[-limit:] if limit > 0 else []


async def append(
    conversation_id: str,
    user_id: str,
    ticker: Optional[str],
    user_message: str,
    assistant_message: str,
) -> None:
    """Add one exchange, trimming the oldest if the cap is reached."""
    stored = await load(conversation_id)
    messages = stored.get("messages") or []
    now = datetime.now(timezone.utc).isoformat()

    messages.append({"role": "user", "content": user_message, "ts": now})
    messages.append({"role": "assistant", "content": assistant_message, "ts": now})

    if len(messages) > MAX_STORED_MESSAGES:
        # Trim in whole exchanges. Dropping one message of a pair leaves an
        # assistant turn whose question is gone, which reads as a non-sequitur
        # to the next model call.
        messages = messages[-MAX_STORED_MESSAGES:]
        if messages and messages[0]["role"] == "assistant":
            messages = messages[1:]

    await save_session(
        _key(conversation_id),
        {
            "conversation_id": conversation_id,
            "user_id": user_id,
            "ticker": ticker,
            "created_at": stored.get("created_at") or now,
            "updated_at": now,
            "messages": messages,
        },
        ttl=SESSION_TTL,
    )


async def clear(conversation_id: str) -> None:
    """Forget a conversation.

    Overwrites with an empty record and a short expiry rather than deleting —
    `memory/session.py` exposes no delete, and adding one for this would widen
    its surface for no gain.
    """
    await save_session(_key(conversation_id), {"messages": []}, ttl=1)
