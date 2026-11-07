"""Alpaca news — Benzinga's newsroom, ticker-tagged, pushed over a websocket.

The closest thing to a professional squawk a free account can get: headlines
arrive seconds after publication, each already tagged with the symbols it is
about. A free Alpaca account (paper trading is enough) provides the keys.

Two paths, one parser, because both carry the same fields:

* **REST** (`/v1beta1/news`) — catch-up after a restart or a dropped stream,
  newest first, 50 a page.
* **Websocket** (`/v1beta1/news` stream) — live. A free account may keep one
  stream connection at a time, which is why the runner takes a lease before
  opening it (services/news/runner.py).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional

import structlog
import websockets

from app.agents.research.sources import _clean, _safe_url
from app.config import get_settings
from app.services.news.base import NewsProvider
from app.services.news.http import SourceBlocked, get_json
from app.services.news.models import Batch, Headline, headline_id

log = structlog.get_logger(__name__)

REST_URL = "https://data.alpaca.markets/v1beta1/news"
STREAM_URL = "wss://stream.data.alpaca.markets/v1beta1/news"
PAGE_LIMIT = 50
MAX_PAGES = 5
MAX_SYMBOLS = 8

# Alpaca's stream error codes that retrying will not fix.
FATAL_CODES = {401: "not authenticated", 402: "authentication failed",
               406: "connection limit exceeded", 409: "insufficient subscription"}


class StreamRefused(Exception):
    def __init__(self, code: Optional[int], msg: str):
        super().__init__(f"{code}: {msg}" if code else msg)
        self.code = code
        self.fatal = code in FATAL_CODES


def _when(value: Any) -> Optional[datetime]:
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def parse_item(item: dict) -> Optional[Headline]:
    """One article from either the REST page or a stream message."""
    title = _clean(item.get("headline"), 300)
    published = _when(item.get("created_at"))
    if not title or item.get("id") is None or published is None:
        return None
    url = item.get("url")
    source = _clean(item.get("source"), 40)
    symbols = [str(s).upper() for s in item.get("symbols") or [] if s][:MAX_SYMBOLS]
    return Headline(
        id=headline_id("alpaca", str(item["id"])),
        source="alpaca",
        source_id=str(item["id"]),
        publisher=source.title() if source else "Alpaca",
        title=title,
        summary=_clean(item.get("summary"), 600) or None,
        url=url if _safe_url(url) else None,
        published_at=published,
        symbols=symbols,
        category="news",
    )


def parse_messages(raw: str | bytes) -> list[dict]:
    """A stream frame is a JSON array of messages, occasionally a bare object."""
    data = json.loads(raw)
    return [m for m in (data if isinstance(data, list) else [data]) if isinstance(m, dict)]


class AlpacaProvider(NewsProvider):
    id = "alpaca"
    label = "Alpaca · Benzinga"
    capabilities = ("headlines", "stream")

    def missing_config(self) -> Optional[str]:
        s = get_settings()
        if not (s.alpaca_api_key_id and s.alpaca_api_secret):
            return ("Needs a free Alpaca account — set ALPACA_API_KEY_ID and "
                    "ALPACA_API_SECRET (paper-trading keys are enough).")
        return None

    def _headers(self) -> dict[str, str]:
        s = get_settings()
        return {"APCA-API-KEY-ID": s.alpaca_api_key_id, "APCA-API-SECRET-KEY": s.alpaca_api_secret}

    async def fetch_since(self, since: datetime) -> Batch:
        """Every article published after `since`, newest first, up to 250."""
        missing = self.missing_config()
        if missing:
            return Batch(source="alpaca", available=False, caveat=missing)
        headlines: list[Headline] = []
        params: dict[str, Any] = {
            "start": since.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "limit": PAGE_LIMIT, "sort": "desc", "include_content": "false",
        }
        try:
            for _ in range(MAX_PAGES):
                page = await get_json(REST_URL, params=params, headers=self._headers())
                headlines += [h for h in map(parse_item, page.get("news") or []) if h]
                token = page.get("next_page_token")
                if not token:
                    break
                params["page_token"] = token
        except SourceBlocked as e:
            self.last_error = f"Alpaca refused the request ({e}); check the API keys."
            return Batch(source="alpaca", available=False, caveat=self.last_error,
                         headlines=headlines)
        except Exception as e:
            log.warning("news_alpaca_fetch_failed", error=str(e)[:200])
            return Batch(source="alpaca", available=False,
                         caveat="Alpaca news could not be read.", headlines=headlines)
        return Batch(source="alpaca", headlines=headlines)

    async def stream(self) -> AsyncIterator[list[Headline]]:
        """Live headlines until the connection drops; raises StreamRefused on refusal."""
        s = get_settings()
        async with websockets.connect(STREAM_URL, open_timeout=10, ping_interval=20,
                                      ping_timeout=20, max_size=2**20) as ws:
            await ws.send(json.dumps({"action": "auth", "key": s.alpaca_api_key_id,
                                      "secret": s.alpaca_api_secret}))
            subscribed = False
            async for raw in ws:
                headlines: list[Headline] = []
                for msg in parse_messages(raw):
                    kind = msg.get("T")
                    if kind == "error":
                        raise StreamRefused(msg.get("code"), str(msg.get("msg", "error")))
                    if kind == "success" and msg.get("msg") == "authenticated" and not subscribed:
                        await ws.send(json.dumps({"action": "subscribe", "news": ["*"]}))
                        subscribed = True
                    elif kind == "subscription":
                        self.last_error = None
                        log.info("news_alpaca_subscribed", news=msg.get("news"))
                    elif kind == "n" and (h := parse_item(msg)):
                        headlines.append(h)
                if headlines:
                    yield headlines
