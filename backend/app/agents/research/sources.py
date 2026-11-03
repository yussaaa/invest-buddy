"""The source book: every citable thing, numbered, built only from tool payloads.

The model never writes a URL. It sees `[S3]` and the text behind it, and may
cite `[S3]`; the link the reader clicks comes from this book, which was filled
from what the tools actually returned. A fabricated citation therefore cannot
point anywhere — it fails to resolve and is stripped in verify.

Everything a source says is third-party text: headlines, search snippets,
filings. It is wrapped in `<untrusted_source>` tags and trimmed before it
reaches the model, and the system prompt says such text is data. That is the
injection boundary for this agent, which unlike chat reads the open web.
"""

from __future__ import annotations

import html
import re
from typing import Any, Optional

from app.agents.base.guardrails import _NUMBER_RE, _parse_number
from app.agents.research.schemas import ResearchSource, SourceKind

MAX_SOURCES = 15
SNIPPET_CHARS = 500


def _clean(text: Any, limit: int) -> str:
    """Flatten whitespace, drop anything tag-like, and trim."""
    flat = re.sub(r"\s+", " ", html.unescape(str(text or ""))).strip()
    flat = re.sub(r"</?[A-Za-z_][^>]{0,200}>", "", flat)
    return flat[:limit]


class SourceBook:
    def __init__(self) -> None:
        self.sources: list[ResearchSource] = []
        self._text: dict[str, str] = {}
        self._seen: set[tuple] = set()

    def add(self, *, kind: SourceKind, title: Any, tool: str, url: Optional[str] = None,
            publisher: Optional[str] = None, published_at: Optional[str] = None,
            text: Any = "") -> Optional[str]:
        title = _clean(title, 200)
        if not title or len(self.sources) >= MAX_SOURCES:
            return None
        key = (url or "", title.lower()) if kind == "filing" else (url or title.lower(),)
        if key in self._seen:
            return None
        self._seen.add(key)

        source_id = f"S{len(self.sources) + 1}"
        self.sources.append(ResearchSource(
            id=source_id, kind=kind, title=title, url=url if _safe_url(url) else None,
            publisher=_clean(publisher, 80) or None, published_at=_clean(published_at, 40) or None,
            tool=tool,
        ))
        self._text[source_id] = _clean(text, SNIPPET_CHARS)
        return source_id

    @property
    def ids(self) -> set[str]:
        return {s.id for s in self.sources}

    def render(self, ids: Optional[list[str]] = None) -> str:
        """The sources as the model sees them — wrapped, trimmed, numbered."""
        chosen = [s for s in self.sources if ids is None or s.id in ids]
        blocks = []
        for s in chosen:
            meta = " ".join(f'{k}="{_clean(v, 60)}"' for k, v in
                            (("kind", s.kind), ("publisher", s.publisher), ("date", s.published_at)) if v)
            body = s.title + (f" — {self._text[s.id]}" if self._text.get(s.id) else "")
            blocks.append(f'<untrusted_source id="{s.id}" {meta}>{body}</untrusted_source>')
        return "\n".join(blocks)

    def numbers(self) -> list[float]:
        """Every figure in the text the model was shown — quotable, so groundable."""
        # Parsed exactly as the grounding check parses prose, so "$480 million"
        # in a headline grounds "$480 million" in the brief.
        found: list[float] = []
        for s in self.sources:
            for chunk in (s.title, self._text.get(s.id, ""), s.published_at or ""):
                for match in _NUMBER_RE.finditer(chunk):
                    value = _parse_number(match.group())
                    if value is not None:
                        found.append(value)
        return found

    # ── payload adapters ───────────────────────────────────────────────────

    def add_payload(self, tool: str, payload: Any, ticker: str) -> list[str]:
        """File whatever is citable in one tool result. Returns the new ids."""
        if not isinstance(payload, dict):
            return []
        added: list[Optional[str]] = []
        if tool == "get_recent_news":
            for a in payload.get("articles") or []:
                added.append(self.add(kind="news", tool=tool, title=a.get("title"), url=a.get("url"),
                                      publisher=a.get("source"), published_at=a.get("published_at"),
                                      text=a.get("description")))
        elif tool == "search_web":
            for r in payload.get("results") or []:
                added.append(self.add(kind="web", tool=tool, title=r.get("title"), url=r.get("url"),
                                      publisher=_host(r.get("url")), published_at=r.get("published_date"),
                                      text=r.get("content")))
        elif tool == "get_sec_filings":
            for f in payload.get("filings") or []:
                added.append(self.add(kind="filing", tool=tool,
                                      title=f"{f.get('filing_type')} filing {f.get('accession_number')}",
                                      url=f.get("url"), publisher="SEC EDGAR",
                                      text=f.get("excerpt")))
        elif tool in ("get_social_sentiment", "social_snapshot"):
            added += self.add_social(payload, ticker, tool)
        elif tool == "get_trending_tickers":
            added.append(self.add(kind="social", tool=tool, url="https://apewisdom.io/",
                                  title=f"Reddit mention board ({payload.get('filter', 'all-stocks')})",
                                  publisher="ApeWisdom"))
        elif tool in ("get_earnings_calendar", "get_company_overview", "quote"):
            added.append(self.add(kind="market_data", tool=tool,
                                  url=f"https://finance.yahoo.com/quote/{ticker}",
                                  title=f"{ticker} market data ({tool.replace('_', ' ')})",
                                  publisher="Yahoo Finance"))
        return [i for i in added if i]

    def add_social(self, payload: dict, ticker: str, tool: str) -> list[Optional[str]]:
        out: list[Optional[str]] = []
        reddit = payload.get("reddit") or {}
        if reddit.get("available") is not False:
            out.append(self.add(kind="social", tool=tool, url=f"https://apewisdom.io/stocks/{ticker}/",
                                title=f"{ticker} on the Reddit mention board", publisher="ApeWisdom"))
        crowd = payload.get("stocktwits") or {}
        if crowd.get("available"):
            out.append(self.add(kind="social", tool=tool, url=f"https://stocktwits.com/symbol/{ticker}",
                                title=f"{ticker} StockTwits stream (self-tagged)", publisher="StockTwits"))
        return out


def _safe_url(url: Optional[str]) -> bool:
    return bool(url) and str(url).startswith(("https://", "http://"))


def _host(url: Optional[str]) -> Optional[str]:
    match = re.match(r"https?://(?:www\.)?([^/]+)", url or "")
    return match.group(1) if match else None
