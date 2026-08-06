"""State for the chat graph.

Separate from `agents/base/state.py:AgentState` on purpose. That one is
single-shot and analysis-shaped — five specialist slots, a final report, a run
id. A chat turn has none of those and needs things it has none of: a working
transcript, an iteration counter, a signal set. Bolting both onto one TypedDict
would leave every node reading fields that can never apply to it.

The reducers are shared, though, so accumulation behaves the same way in both
graphs.
"""

from __future__ import annotations

from typing import Annotated, Any, Optional, TypedDict

from app.agents.base.schemas import GuardrailFlag
from app.agents.base.state import _append_list, _merge_dicts
from app.agents.chat.schemas import ScreenContext
from app.services.signals import SignalSet


class ChatState(TypedDict, total=False):
    # ── Input ───────────────────────────────────────────────────────────────
    conversation_id: str
    user_id: str
    message: str
    screen: ScreenContext
    history: list[dict]          # prior turns, already OpenAI-shaped

    # ── Prepared before the first model call ────────────────────────────────
    ticker: str
    technicals: dict
    quote: Optional[dict]
    profile: Optional[dict]
    signal_set: Optional[SignalSet]

    # ── The agent loop ──────────────────────────────────────────────────────
    # `messages` is this turn's working transcript: the system prompt, the
    # user's question, and every tool call and result the loop adds.
    messages: Annotated[list[dict], _append_list]
    tool_iterations: int
    tool_records: Annotated[list[dict], _append_list]

    # ── Output ──────────────────────────────────────────────────────────────
    answer: str
    signals_cited: list[str]
    flags: Annotated[list[GuardrailFlag], _append_list]
    blocked: bool
    usage: Annotated[dict[str, int], _merge_dicts]
    model: str
    latency_breakdown: Annotated[dict[str, float], _merge_dicts]
    error: Optional[str]


def new_state(
    *,
    conversation_id: str,
    user_id: str,
    message: str,
    screen: ScreenContext,
    history: Optional[list[dict]] = None,
) -> ChatState:
    """A fully populated initial state.

    Every reducer-annotated field starts as an empty container: LangGraph
    applies reducers to whatever is already there, and a missing key would make
    the first node's update the whole value rather than an addition to it.
    """
    return ChatState(
        conversation_id=conversation_id,
        user_id=user_id,
        message=message,
        screen=screen,
        history=history or [],
        ticker=(screen.ticker or "").upper(),
        technicals={},
        quote=None,
        profile=None,
        signal_set=None,
        messages=[],
        tool_iterations=0,
        tool_records=[],
        answer="",
        signals_cited=[],
        flags=[],
        blocked=False,
        usage={},
        model="",
        latency_breakdown={},
        error=None,
    )
