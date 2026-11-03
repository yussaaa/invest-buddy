"""The research agent: why is this ticker in focus?

    prepare ──▶ gather ◀──▶ tools ──▶ answer (streamed) ──▶ verify

The same bounded cycle as the chat agent (agents/chat/graph.py), pointed
outward. Chat reads the chart on screen and mostly answers from a
pre-computed signal set; this reads the open web, news and online discussion
and has to show its sources. Three things follow from that:

* **Citations are ids, not links.** Every citable thing is numbered in a
  `SourceBook` built from tool payloads. The model cites `[S3]`; the URL comes
  from the book. A citation that does not resolve is stripped, and a driver
  left with none is dropped — enforced here, not merely checked.
* **Third-party text is fenced.** Headlines, snippets and boards arrive in
  `<untrusted_source>` tags, trimmed, and the prompt says they are data.
* **Verify can replace the answer.** Advice wording or a figure found in no
  source swaps the model's brief for a templated one built from the same
  sources and the deterministic crowd conditions, flagged `guarded`.

Walked by hand as an async generator rather than compiled with LangGraph, for
the reason `stream_chat_turn` gives: token streaming has to reach inside the
answer step. `run_research` drains the same generator, so there is one path.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional

import structlog

from app.agents.base.guardrails import check_investment_advice, check_numeric_grounding
from app.agents.base.schemas import GuardrailFlag
from app.agents.base.tool_loop import (
    assistant_message,
    run_tool_calls,
    signature,
)
from app.agents.research.prompts import (
    GATHER_INSTRUCTION,
    RESEARCH_SYSTEM_PROMPT,
    RESEARCH_TOOL_NAMES,
)
from app.agents.research.schemas import ResearchBrief, ResearchFinding
from app.agents.research.sources import SourceBook
from app.config import get_settings
from app.db.repositories_market import MARKET_TZ
from app.models.factory import get_provider
from app.services import cache, market_data, signals_page
from app.tools.registry import ToolRegistry
from app.tools.social.social_tool import rank_movement

log = structlog.get_logger(__name__)

BRIEF_TTL = 1800.0
GATHER_MAX_TOKENS = 400
ANSWER_MAX_TOKENS = 900
DATA_BLOCK_CHARS = 2500

# Tools whose results are citable documents rather than data blocks.
_SOURCE_TOOLS = {"search_web", "get_recent_news", "get_sec_filings"}

_CITE = re.compile(r"\[(S\d{1,2})\]")


def _model_name(settings) -> str:
    return settings.smart_model if settings.research_model == "smart" else settings.fast_model


def cache_key(ticker: str, question: str, provider: str) -> str:
    digest = hashlib.sha1(question.strip().lower().encode()).hexdigest()[:10]
    return f"research:{provider}:{ticker}:{digest}"


def default_question(ticker: str) -> str:
    return f"Why is {ticker} in focus right now?"


# ── 1. Prepare ─────────────────────────────────────────────────────────────


class Run:
    """Mutable state for one research run."""

    def __init__(self, ticker: str, question: str) -> None:
        self.ticker = ticker
        self.question = question
        self.book = SourceBook()
        self.snapshot: dict = {}
        self.quote: dict = {}
        self.data_blocks: dict[str, Any] = {}      # tool → payload, non-source tools
        self.payloads: list[Any] = []              # everything, for grounding
        self.executed: set[str] = set()            # call signatures already run
        self.web_searches = 0
        self.messages: list[dict] = []             # the gather transcript
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0}


async def _registry_call(name: str, args: dict) -> Any:
    result = await ToolRegistry.get().execute(name, args)
    return result.data if result.success else {"error": result.error}


async def prepare(run: Run) -> None:
    """Everything fetched before the model is asked anything. No LLM here."""
    ticker = run.ticker
    query = f"{ticker} stock news why moving"
    news_args = {"ticker": ticker, "days": 3, "max_results": 8}
    web_args = {"query": query, "max_results": 5}

    snapshot, quotes, news, web = await asyncio.gather(
        signals_page.get_ticker_snapshot(ticker),
        market_data.get_quotes([ticker]),
        _registry_call("get_recent_news", news_args),
        _registry_call("search_web", web_args),
        return_exceptions=True,
    )
    run.executed |= {signature("get_recent_news", news_args), signature("search_web", web_args)}
    run.web_searches += 1

    run.snapshot = snapshot if isinstance(snapshot, dict) and "error" not in snapshot else {}
    run.quote = (quotes[0] if isinstance(quotes, list) and quotes else {}) or {}
    if run.snapshot.get("name") and run.snapshot["name"] != ticker:
        run.data_blocks["company"] = {"name": run.snapshot["name"],
                                      "sector": run.snapshot.get("sector")}

    if run.quote:
        run.book.add_payload("quote", run.quote, ticker)
    for name, payload in (("get_recent_news", news), ("search_web", web)):
        if isinstance(payload, dict):
            run.book.add_payload(name, payload, ticker)
            run.payloads.append(payload)
            if payload.get("caveat") or payload.get("error"):
                run.data_blocks[f"{name}_status"] = payload.get("caveat") or payload.get("error")
    if run.snapshot:
        run.book.add_payload("social_snapshot", run.snapshot, ticker)
        run.payloads.append(run.snapshot)


def _conditions(signal_set: Optional[dict]) -> list[dict]:
    return [
        {"id": s.get("id"), "label": s.get("label"), "direction": s.get("direction"),
         "rationale": s.get("rationale"), "evidence": s.get("evidence")}
        for s in (signal_set or {}).get("signals", [])
    ]


def context_block(run: Run) -> str:
    snap = run.snapshot
    quote = {k: run.quote.get(k) for k in ("last", "change", "change_percent", "volume")} if run.quote else None
    crowd = {
        "reddit": {k: (snap.get("reddit") or {}).get(k) for k in (
            "available", "caveat", "rank", "rank_24h_ago", "mentions", "mentions_24h_ago",
            "mention_change_pct", "attention_z")} | {
            "window_hours": 24,
            "rank_movement": rank_movement((snap.get("reddit") or {}).get("rank"),
                                           (snap.get("reddit") or {}).get("rank_24h_ago")),
        },
        "stocktwits": {k: (snap.get("stocktwits") or {}).get(k) for k in (
            "available", "caveat", "bullish", "bearish", "bull_pct")},
        "conditions": _conditions(snap.get("social_signal_set")),
    } if snap else {"unavailable": True}
    technical = [
        {"id": c["id"], "label": c["label"], "direction": c["direction"], "rationale": c["rationale"]}
        for c in _conditions(snap.get("technical_signal_set"))
    ]
    data = {k: json.dumps(v, default=str)[:DATA_BLOCK_CHARS] for k, v in run.data_blocks.items()}

    # The model's sense of "now" is its training cutoff; without the date it
    # searches for last year's quarter and calls it recent.
    today = datetime.now(MARKET_TZ).strftime("%A %d %B %Y")
    parts = [
        f"TODAY: {today} (New York)",
        f"TICKER: {run.ticker}",
        f"QUESTION: {run.question}",
        f"QUOTE: {json.dumps(quote, default=str)}",
        f"CROWD (deterministic): {json.dumps(crowd, default=str)}",
        f"TECHNICAL CONDITIONS (deterministic): {json.dumps(technical, default=str)}",
    ]
    if data:
        parts.append("DATA:\n" + "\n".join(f"{k}: {v}" for k, v in data.items()))
    parts.append("SOURCES:\n" + (run.book.render() or "(none could be retrieved)"))
    return "\n\n".join(parts)


# ── 2. Gather ⇄ tools ──────────────────────────────────────────────────────


def _tool_schemas() -> list[dict]:
    provider = get_provider()
    if not provider.supports_tools:
        return []
    return ToolRegistry.get().get_schema_for_llm(RESEARCH_TOOL_NAMES)


def _add_usage(run: Run, response) -> None:
    run.usage["prompt_tokens"] += getattr(response, "prompt_tokens", 0) or 0
    run.usage["completion_tokens"] += getattr(response, "completion_tokens", 0) or 0


async def gather(run: Run, settings) -> list:
    """One model turn deciding what else to fetch. Returns the calls to run."""
    tools = _tool_schemas()
    if not tools:
        return []
    try:
        response = await get_provider().complete(
            messages=[
                {"role": "system", "content": RESEARCH_SYSTEM_PROMPT},
                {"role": "user", "content": context_block(run)},
                *run.messages,
                {"role": "user", "content": GATHER_INSTRUCTION},
            ],
            model=_model_name(settings), temperature=0.0,
            max_tokens=GATHER_MAX_TOKENS, tools=tools, tool_choice="auto",
        )
    except Exception as e:
        log.warning("research_gather_failed", error=str(e))
        return []
    _add_usage(run, response)

    calls = []
    for call in response.tool_calls:
        if call.name not in RESEARCH_TOOL_NAMES:
            continue
        sig = signature(call.name, call.arguments)
        if sig in run.executed:
            continue
        if call.name == "search_web":
            if run.web_searches >= settings.research_max_web_searches:
                continue
            run.web_searches += 1
        run.executed.add(sig)
        calls.append(call)
    calls = calls[: settings.research_max_tool_calls]
    if calls:
        run.messages.append(assistant_message(response.content, calls))
    return calls


async def execute(run: Run, calls: list) -> list[dict]:
    """Run calls; file their sources; feed the model fenced text, not raw JSON."""
    result = await run_tool_calls(calls)
    for message, payload in zip(result.messages, result.payloads):
        name, data = payload["name"], payload["data"]
        run.payloads.append(data)
        new_ids = run.book.add_payload(name, data, run.ticker)
        if name in _SOURCE_TOOLS:
            message["content"] = (run.book.render(new_ids) if new_ids
                                  else json.dumps({"note": "nothing new or citable",
                                                   "caveat": (data or {}).get("caveat")
                                                   if isinstance(data, dict) else None}))
        else:
            run.data_blocks[name] = data
    run.messages.extend(result.messages)
    return result.records


# ── 3. Answer ──────────────────────────────────────────────────────────────


def answer_messages(run: Run) -> list[dict]:
    """The narration call sees the final context, not the tool transcript."""
    return [
        {"role": "system", "content": RESEARCH_SYSTEM_PROMPT},
        {"role": "user", "content": context_block(run) + "\n\nWrite the brief now."},
    ]


# ── 4. Verify ──────────────────────────────────────────────────────────────


def _norm_heading(text: str) -> str:
    return re.sub(r"[^a-z ]", "", text.lower().replace("’", "'").replace("'", "")).strip()


_HEADINGS = {
    "headline": "headline",
    "why its in focus": "drivers", "why it is in focus": "drivers",
    "what the crowd shows": "crowd",
    "what would change the picture": "change",
    "what isnt known": "unknowns", "what is not known": "unknowns",
}


def parse_sections(text: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        match = re.match(r"^\s*#{1,4}\s*(.+?)\s*$", line)
        if match:
            current = _HEADINGS.get(_norm_heading(match.group(1)))
            if current:
                sections.setdefault(current, [])
            continue
        if current:
            sections[current].append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items()}


def bullets(text: str) -> list[str]:
    items = []
    for line in text.splitlines():
        match = re.match(r"^\s*(?:[-*•]|\d+[.)])\s+(.*\S)", line)
        if match:
            items.append(match.group(1))
    return items or ([text.strip()] if text.strip() else [])


def _strip_markers(text: str) -> str:
    """Plain text for the structured fields: no citation markers, no bold.

    `markdown` keeps both; the parsed fields are rendered as text, where a
    literal `**` reads as a bug.
    """
    text = re.sub(r"\s*\[S\d{1,2}\]", "", text)
    return text.replace("**", "").strip()


def grounding_evidence(run: Run) -> dict:
    return {
        # Mention counts are rolling 24-hour windows; "in 24 hours" is a
        # property of the source, not a reading, but it is still a number.
        "windows": {"reddit_window_hours": 24},
        "snapshot": run.snapshot,
        "quote": run.quote,
        "payloads": run.payloads,
        "source_numbers": run.book.numbers(),
        "data": run.data_blocks,
    }


def verify(run: Run, draft: str) -> tuple[ResearchBrief, bool]:
    """Strip phantom citations, drop uncited drivers, and block on advice or invention."""
    flags: list[GuardrailFlag] = []
    known = run.book.ids

    def resolve(match: re.Match) -> str:
        if match.group(1) in known:
            return match.group(0)
        flags.append(GuardrailFlag(flag_type="missing_citation", agent="research",
                                   detail=f"Removed citation {match.group(1)}: no such source.",
                                   severity="warning"))
        return ""

    cleaned = _CITE.sub(resolve, draft or "").strip()
    sections = parse_sections(cleaned)

    drivers: list[ResearchFinding] = []
    for item in bullets(sections.get("drivers", "")):
        ids = list(dict.fromkeys(_CITE.findall(item)))
        if not ids:
            flags.append(GuardrailFlag(flag_type="missing_citation", agent="research",
                                       detail=f"Dropped an uncited driver: {item[:80]}",
                                       severity="warning"))
            continue
        drivers.append(ResearchFinding(text=_strip_markers(item), source_ids=ids))

    prose = _strip_markers(cleaned)
    advice = check_investment_advice(prose, mode="chat")
    ungrounded = check_numeric_grounding(prose, grounding_evidence(run))
    for hit in advice:
        flags.append(GuardrailFlag(flag_type="investment_advice", agent="research",
                                   detail=f"Blocked: {hit}", severity="error"))
    for figure in ungrounded:
        flags.append(GuardrailFlag(flag_type="hallucination", agent="research",
                                   detail=f"Blocked: figure {figure} appears in no source.",
                                   severity="error"))
    if not cleaned:
        flags.append(GuardrailFlag(flag_type="data_unavailable", agent="research",
                                   detail="The model returned no brief.", severity="warning"))

    # A brief with sources to cite but no cited driver is an uncited essay.
    uncited = not drivers and bool(run.book.sources)
    blocked = bool(advice or ungrounded or not cleaned or uncited)
    if blocked:
        log.warning("research_brief_guarded", ticker=run.ticker, advice=len(advice),
                    ungrounded=len(ungrounded), drivers=len(drivers))
        brief = templated_brief(run)
        brief.flags = flags
        return brief, True

    headline = _strip_markers(sections.get("headline", "")).splitlines()
    return ResearchBrief(
        ticker=run.ticker, question=run.question, as_of=datetime.now(timezone.utc),
        headline=(headline[0] if headline else drivers[0].text if drivers else ""),
        drivers=drivers[:5],
        crowd_read=_strip_markers(sections.get("crowd", "")),
        what_would_change=[_strip_markers(b) for b in bullets(sections.get("change", ""))][:4],
        unknowns=[_strip_markers(b) for b in bullets(sections.get("unknowns", ""))][:4],
        sources=run.book.sources,
        social_signal_set=run.snapshot.get("social_signal_set"),
        markdown=cleaned,
        flags=flags,
    ), False


def templated_brief(run: Run) -> ResearchBrief:
    """What the reader gets when the model's brief may not be shown.

    Built from source titles and deterministic rationales only — every word
    traces to a payload, so it grounds and stays clean by construction.
    """
    documents = [s for s in run.book.sources if s.kind in ("news", "web", "filing")][:4]
    drivers = [ResearchFinding(text=f"Coverage: “{s.title}”" + (f" ({s.publisher})" if s.publisher else ""),
                               source_ids=[s.id]) for s in documents]
    social = (run.snapshot.get("social_signal_set") or {}).get("signals", [])
    crowd = " ".join(s.get("rationale", "") for s in social[:3]) or (
        "No unusual crowd conditions were found." if run.snapshot else
        "Crowd data could not be retrieved.")
    unknowns = [c for c in ((run.snapshot.get("reddit") or {}).get("caveat"),
                            (run.snapshot.get("stocktwits") or {}).get("caveat")) if c]
    if not documents:
        unknowns.append("No news or web sources could be retrieved for this run.")
    return ResearchBrief(
        ticker=run.ticker, question=run.question, as_of=datetime.now(timezone.utc),
        headline=(f"Recent coverage of {run.ticker} is listed below; the sources did not "
                  f"support a written summary that passed review." if documents else
                  f"No sources explaining attention on {run.ticker} could be retrieved."),
        drivers=drivers,
        crowd_read=crowd,
        what_would_change=[s.get("invalidation") for s in social[:3] if s.get("invalidation")],
        unknowns=unknowns,
        sources=run.book.sources,
        social_signal_set=run.snapshot.get("social_signal_set"),
        guarded=True,
    )


# ── Driver ─────────────────────────────────────────────────────────────────


async def stream_research(
    ticker: str, question: Optional[str] = None,
) -> AsyncIterator[tuple[str, dict]]:
    """Yield (event, payload) for one research run.

    Events: start, context, sources, tool_call, tool_result, token, flag,
    done, error. `done.brief` is final; `done.replaced` tells the client to
    discard the streamed draft.
    """
    settings = get_settings()
    t0 = time.monotonic()
    ticker = ticker.upper().strip()
    question = (question or "").strip() or default_question(ticker)
    key = cache_key(ticker, question, settings.active_provider)

    cached_brief = cache.get_fresh(key, BRIEF_TTL) or (await cache.get_fresh_many([key], BRIEF_TTL)).get(key)
    if cached_brief:
        yield "start", {"ticker": ticker, "question": question, "cached": True}
        yield "done", {"brief": cached_brief, "replaced": False, "cached": True,
                       "latency_ms": int((time.monotonic() - t0) * 1000)}
        return

    yield "start", {"ticker": ticker, "question": question, "cached": False}
    run = Run(ticker, question)
    try:
        await prepare(run)
        yield "context", {
            "social_signal_set": run.snapshot.get("social_signal_set"),
            "technical_signal_set": run.snapshot.get("technical_signal_set"),
        }
        yield "sources", {"sources": [s.model_dump() for s in run.book.sources]}

        for _ in range(settings.research_max_tool_iterations):
            if time.monotonic() - t0 > settings.research_timeout_seconds:
                log.info("research_budget_spent", ticker=ticker)
                break
            calls = await gather(run, settings)
            if not calls:
                break
            for call in calls:
                yield "tool_call", {"name": call.name, "arguments": call.arguments}
            before = len(run.book.sources)
            for record in await execute(run, calls):
                yield "tool_result", {k: record.get(k) for k in
                                      ("name", "success", "latency_ms", "cache_hit", "error")}
            if len(run.book.sources) != before:
                yield "sources", {"sources": [s.model_dump() for s in run.book.sources]}

        chunks: list[str] = []

        def on_usage(prompt: int, completion: int) -> None:
            run.usage["prompt_tokens"] += prompt
            run.usage["completion_tokens"] += completion

        async for chunk in get_provider().stream(
            messages=answer_messages(run), model=_model_name(settings),
            temperature=0.2, max_tokens=ANSWER_MAX_TOKENS, on_usage=on_usage,
        ):
            chunks.append(chunk)
            yield "token", {"text": chunk}
    except Exception as e:
        log.error("research_failed", ticker=ticker, error=str(e), exc_info=True)
        yield "error", {"message": "The research run could not complete."}
        return

    brief, replaced = verify(run, "".join(chunks))
    brief.model = _model_name(settings)
    for flag in brief.flags:
        yield "flag", flag.model_dump(mode="json")

    payload = brief.model_dump(mode="json")
    if not replaced:
        await cache.prime(key, payload, BRIEF_TTL)
    yield "done", {"brief": payload, "replaced": replaced, "cached": False,
                   "usage": run.usage, "latency_ms": int((time.monotonic() - t0) * 1000)}


async def run_research(ticker: str, question: Optional[str] = None) -> dict:
    """The same run, collected. For the JSON endpoint and tests."""
    events: list[tuple[str, dict]] = []
    async for event, payload in stream_research(ticker, question):
        events.append((event, payload))
    for event, payload in reversed(events):
        if event == "done":
            return {**payload, "events": [e for e, _ in events]}
        if event == "error":
            return {"error": payload.get("message"), "events": [e for e, _ in events]}
    return {"error": "no result"}
