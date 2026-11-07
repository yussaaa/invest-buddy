"""Federal Reserve press releases — official, keyless, RSS.

Rate decisions, FOMC statements and minutes, bank enforcement and merger
approvals. The FOMC statement lands here at 2:00pm ET on decision days, which
is the single most market-moving release on the calendar.

No tickers: a Fed release is about the market, not a company. Triage decides
what it touches.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import structlog

from app.agents.research.sources import _clean, _safe_url
from app.services.news.base import NewsProvider
from app.services.news.http import SourceBlocked, get_text
from app.services.news.models import Batch, Headline, headline_id

log = structlog.get_logger(__name__)

FEED_URL = "https://www.federalreserve.gov/feeds/press_all.xml"

# The Fed's own release categories, mapped onto ours.
CATEGORY_MAP = {
    "Monetary Policy": "monetary_policy",
    "Banking and Consumer Regulatory Policy": "regulatory",
    "Enforcement Actions": "regulatory",
    "Orders on Banking Applications": "regulatory",
}


def _text(item: ET.Element, tag: str) -> str:
    node = item.find(tag)
    return (node.text or "").strip() if node is not None else ""


def parse_feed(xml: str) -> list[Headline]:
    """Every item in the feed; unparseable items are skipped, not fatal."""
    # The Fed serves a byte-order mark before the declaration.
    root = ET.fromstring(xml.lstrip("﻿").strip())
    out: list[Headline] = []
    for item in root.iter("item"):
        title = _clean(_text(item, "title"), 300)
        link = _text(item, "link")
        guid = _text(item, "guid") or link
        try:
            published = parsedate_to_datetime(_text(item, "pubDate"))
        except (TypeError, ValueError):
            continue
        if not title or not guid:
            continue
        fed_category = _text(item, "category")
        description = _clean(_text(item, "description"), 600)
        out.append(Headline(
            id=headline_id("fed", guid),
            source="fed",
            source_id=guid[:80],
            publisher="Federal Reserve",
            title=title,
            # The Fed repeats the title as the description; that is no summary.
            summary=description if description and description != title else None,
            url=link if _safe_url(link) else None,
            published_at=published,
            category=CATEGORY_MAP.get(fed_category, "news"),
            tags=[fed_category] if fed_category else [],
        ))
    return out


class FedProvider(NewsProvider):
    id = "fed"
    label = "Federal Reserve"
    capabilities = ("macro", "poll")

    async def poll(self) -> Batch:
        try:
            xml = await get_text(FEED_URL, headers={"Accept": "application/rss+xml, text/xml"})
            headlines = parse_feed(xml)
        except SourceBlocked as e:
            self.last_error = f"The Fed feed refused the request ({e})."
            return Batch(source="fed", available=False, caveat=self.last_error)
        except Exception as e:
            log.warning("news_fed_poll_failed", error=str(e)[:200])
            self.last_error = "The Fed feed could not be read."
            return Batch(source="fed", available=False, caveat=self.last_error)
        self.last_error = None
        return Batch(source="fed", headlines=headlines)
