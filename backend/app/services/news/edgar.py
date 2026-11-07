"""SEC EDGAR 8-K filings — official, keyless, within minutes of acceptance.

An 8-K is how a listed company tells the market something material between
quarterly reports: results, deals, a CEO leaving, a restatement, a delisting
notice. The "current filings" Atom feed lists them as they are accepted.

Two filters keep the wire readable:

* **Exchange-listed companies only.** The feed carries every filer — shells,
  trusts, private issuers, OTC names. A CIK without a Nasdaq/NYSE/Cboe
  listing in the SEC's own ticker map is dropped.
* **Material items only.** Most 8-Ks are a press release (7.01), "other
  events" (8.01) or a shareholder vote (5.07). Those reach the wire through
  Alpaca's newsroom feed with a real headline; here they would only be noise.

EDGAR's fair-access policy refuses a User-Agent without a contact address, so
this source stays off until a contact is set — on the Settings page, or
SEC_USER_AGENT in .env (services/app_settings.py).
"""

from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Optional

import structlog

from app.agents.research.sources import _clean, _safe_url
from app.services import app_settings
from app.services.cache import cached_async
from app.services.news.base import NewsProvider
from app.services.news.http import SourceBlocked, get_json, get_text
from app.services.news.models import Batch, Headline, headline_id

log = structlog.get_logger(__name__)

FEED_URL = "https://www.sec.gov/cgi-bin/browse-edgar"
FEED_PARAMS = {"action": "getcurrent", "type": "8-K", "owner": "include",
               "count": "100", "output": "atom"}
TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
# OTC and unlisted filers are dropped: shells and trusts the wire cannot price.
LISTED_EXCHANGES = frozenset({"Nasdaq", "NYSE", "CBOE", "TXSE"})
TICKERS_TTL = 86_400.0

ATOM = "{http://www.w3.org/2005/Atom}"
_TITLE = re.compile(r"^(?P<form>\S+) - (?P<name>.+?) \((?P<cik>\d{4,10})\) \((?P<role>[^)]+)\)$")
_ITEM = re.compile(r"Item (\d\.\d{2}):\s*([^\n<]+)")
_ACCESSION = re.compile(r"accession-number=([\d-]+)")

# The 8-K items worth a line on the wire: (category, short label). Listed in
# precedence order — a filing reporting results *and* an officer change is an
# earnings headline.
MATERIAL_ITEMS: dict[str, tuple[str, str]] = {
    "1.03": ("corporate", "bankruptcy or receivership"),
    "4.02": ("accounting", "prior financials no longer reliable"),
    "2.02": ("earnings", "results of operations"),
    "5.01": ("deal", "change in control"),
    "2.01": ("deal", "acquisition or disposal completed"),
    "1.05": ("corporate", "cybersecurity incident"),
    "2.06": ("accounting", "material impairment"),
    "3.01": ("corporate", "delisting or listing-rule notice"),
    "1.02": ("deal", "material agreement terminated"),
    "1.01": ("deal", "material agreement"),
    "2.05": ("corporate", "exit or restructuring costs"),
    "4.01": ("accounting", "auditor change"),
    "5.02": ("management", "director or officer change"),
}
# Primary listing only. The SEC map lists preferreds and other classes under
# one CIK, and some of those symbols belong to unrelated common stocks
# elsewhere (Strive's CIK carries SATS, which is EchoStar's).
MAX_SYMBOLS = 1


def ticker_map(raw: dict) -> dict[str, list[str]]:
    """CIK → exchange-listed tickers, from the SEC's company_tickers_exchange.json.

    The file is columnar — {"fields": [...], "data": [[cik, name, ticker,
    exchange], ...]}. Several symbols can share one CIK; the file orders the
    primary listing first, and so does the result.
    """
    fields = (raw or {}).get("fields") or []
    try:
        i_cik, i_ticker, i_exchange = (fields.index(f) for f in ("cik", "ticker", "exchange"))
    except ValueError:
        return {}
    out: dict[str, list[str]] = {}
    for row in (raw or {}).get("data") or []:
        try:
            if row[i_exchange] not in LISTED_EXCHANGES:
                continue
            out.setdefault(str(int(row[i_cik])), []).append(str(row[i_ticker]).upper())
        except (IndexError, TypeError, ValueError):
            continue
    return out


def _entry_text(entry: ET.Element, tag: str) -> str:
    node = entry.find(f"{ATOM}{tag}")
    return (node.text or "").strip() if node is not None else ""


def parse_feed(xml: str, tickers: dict[str, list[str]]) -> list[Headline]:
    root = ET.fromstring(xml)
    out: list[Headline] = []
    seen: set[str] = set()
    for entry in root.iter(f"{ATOM}entry"):
        match = _TITLE.match(_entry_text(entry, "title"))
        if not match or match["form"] != "8-K":
            continue  # amendments (8-K/A) restate; the original was the news
        symbols = tickers.get(str(int(match["cik"])), [])[:MAX_SYMBOLS]
        if not symbols:
            continue

        summary_html = _entry_text(entry, "summary")
        items = [(code, desc.strip()) for code, desc in _ITEM.findall(summary_html)]
        material = [code for code in MATERIAL_ITEMS if code in {c for c, _ in items}]
        if not material:
            continue

        accession = _ACCESSION.search(_entry_text(entry, "id"))
        if not accession or accession[1] in seen:
            continue
        seen.add(accession[1])
        try:
            published = datetime.fromisoformat(_entry_text(entry, "updated"))
        except ValueError:
            continue

        link = entry.find(f"{ATOM}link")
        url = link.get("href") if link is not None else None
        name = _clean(match["name"], 120)
        labels = ", ".join(MATERIAL_ITEMS[code][1] for code in material)
        out.append(Headline(
            id=headline_id("edgar", accession[1]),
            source="edgar",
            source_id=accession[1],
            publisher="SEC EDGAR",
            title=f"{name} ({symbols[0]}) 8-K: {labels}",
            summary=_clean("; ".join(f"Item {c}: {d}" for c, d in items), 600) or None,
            url=url if _safe_url(url) else None,
            published_at=published,
            symbols=symbols,
            category=MATERIAL_ITEMS[material[0]][0],
            tags=[f"8-K {code}" for code, _ in items],
        ))
    return out


class EdgarProvider(NewsProvider):
    id = "edgar"
    label = "SEC 8-K"
    capabilities = ("filings", "poll")

    def missing_config(self) -> Optional[str]:
        if not app_settings.sec_contact()[0]:
            return ("The SEC requires a contact on every request — add one under "
                    "Settings → News wire (or SEC_USER_AGENT in .env).")
        return None

    def _headers(self) -> dict[str, str]:
        return {"User-Agent": app_settings.sec_contact()[0]}

    async def _tickers(self) -> dict[str, list[str]]:
        async def fetch() -> dict[str, list[str]]:
            return ticker_map(await get_json(TICKERS_URL, headers=self._headers()))

        return await cached_async("news:sec_tickers", TICKERS_TTL, fetch,
                                  should_cache=lambda m: len(m) > 1000)

    async def poll(self) -> Batch:
        missing = self.missing_config()
        if missing:
            return Batch(source="edgar", available=False, caveat=missing)
        try:
            xml, tickers = await asyncio.gather(
                get_text(FEED_URL, params=FEED_PARAMS, headers=self._headers()),
                self._tickers(),
            )
            headlines = parse_feed(xml, tickers)
        except SourceBlocked as e:
            self.last_error = f"EDGAR refused the request ({e}); check SEC_USER_AGENT."
            return Batch(source="edgar", available=False, caveat=self.last_error)
        except Exception as e:
            log.warning("news_edgar_poll_failed", error=str(e)[:200])
            self.last_error = "The EDGAR feed could not be read."
            return Batch(source="edgar", available=False, caveat=self.last_error)
        self.last_error = None
        return Batch(source="edgar", headlines=headlines)
