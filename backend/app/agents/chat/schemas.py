"""Request and response shapes for the chat agent.

`Signal` and `SignalSet` are re-exported from `services/signals.py` rather than
defined here. The engine is pure domain logic with no model in it, so it lives
under services/; importing the other way would put an agents/ dependency inside
a service.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from app.agents.base.schemas import GuardrailFlag
from app.services.signals import Signal, SignalSet  # noqa: F401  (re-export)


class HoveredBar(BaseModel):
    """The candle under the crosshair, if the user is pointing at one.

    The single piece of screen state the backend cannot reconstruct from the
    ticker alone, which is why it is worth sending.
    """

    time: Any = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: Optional[float] = None


class ScreenContext(BaseModel):
    """What the user is looking at.

    A descriptor, never the data. The backend re-derives indicators, quote and
    profile from `ticker` through services that are already cached; shipping
    the candle array would move five years of bars the store already holds.
    """

    route: Optional[str] = None
    ticker: Optional[str] = None
    range: Optional[str] = None
    chart_type: Optional[str] = None
    ma_periods: list[int] = Field(default_factory=list)
    hovered_bar: Optional[HoveredBar] = None


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: Optional[str] = None
    user_id: str = "anonymous"
    screen_context: ScreenContext = Field(default_factory=ScreenContext)


class ToolActivity(BaseModel):
    """One tool the agent ran while answering."""

    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    success: bool = True
    error: Optional[str] = None
    latency_ms: int = 0
    cache_hit: bool = False


class ChatMessageOut(BaseModel):
    message_id: str
    role: Literal["assistant"] = "assistant"
    content: str
    signals_cited: list[str] = Field(default_factory=list)
    flags: list[GuardrailFlag] = Field(default_factory=list)
    blocked: bool = Field(
        False,
        description="True when the drafted reply was replaced. The substitute "
        "is built from the signal set, so the user still gets an answer.",
    )
    disclaimer: str


class ChatResponse(BaseModel):
    conversation_id: str
    message: ChatMessageOut
    signal_set: Optional[SignalSet] = None
    tool_calls: list[ToolActivity] = Field(default_factory=list)
    model: str = ""
    usage: dict[str, int] = Field(default_factory=dict)
    latency_ms: int = 0


class ChatHistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    ts: Optional[datetime] = None


class ChatHistory(BaseModel):
    conversation_id: str
    ticker: Optional[str] = None
    messages: list[ChatHistoryMessage] = Field(default_factory=list)
