"""Live smoke tests for the social providers.

Marked `network`: they catch an upstream shape change and nothing else, so
they stay out of the merge gate. StockTwits refusing us is an accepted state
(Cloudflare), so it may come back unavailable — but never raise.
"""

import pytest

pytestmark = pytest.mark.network


async def test_apewisdom_board_parses():
    from app.services.social import apewisdom

    result = await apewisdom.trending("all-stocks", 20)
    assert result.available, result.caveat
    assert result.rows and result.rows[0].rank == 1
    assert all(r.mentions >= 0 for r in result.rows)


async def test_stocktwits_answers_or_says_why_not():
    from app.services.social import stocktwits

    reading = await stocktwits.ticker("AAPL")
    if reading.available:
        assert reading.sampled > 0
    else:
        assert reading.caveat


async def test_preset_screen_returns_rows():
    from app.services import screens

    result = await screens.get_screen("most_actives", 10)
    assert result.get("rows"), result.get("error")
