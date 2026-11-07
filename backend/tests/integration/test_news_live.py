"""Live smoke tests against the real news sources.

Run with: pytest tests/integration/test_news_live.py -m network
They check the parsers still match what the sources actually serve — the
unit tests pin recorded payloads, which cannot notice a format change.
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.services.news import ALPACA, EDGAR, FED
from app.services.news.alpaca import _when

pytestmark = pytest.mark.network


async def test_fed_feed_parses():
    batch = await FED.poll()
    assert batch.available, batch.caveat
    assert batch.headlines and all(h.url for h in batch.headlines)


async def test_edgar_feed_parses():
    if not get_settings().sec_user_agent:
        pytest.skip("SEC_USER_AGENT is not set")
    batch = await EDGAR.poll()
    assert batch.available, batch.caveat
    # Outside filing hours the material, listed subset can be empty; the
    # parse itself succeeding is the check.
    assert all(h.symbols for h in batch.headlines)


async def test_alpaca_rest_returns_recent_news():
    if ALPACA.missing_config():
        pytest.skip("Alpaca keys are not set")
    from datetime import datetime, timedelta, timezone

    batch = await ALPACA.fetch_since(datetime.now(timezone.utc) - timedelta(days=2))
    assert batch.available, batch.caveat
    assert batch.headlines and batch.headlines[0].published_at >= _when("2000-01-01T00:00:00Z")
