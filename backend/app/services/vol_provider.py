"""Where volatility context comes from, behind one swappable seam.

The problem this exists to solve: **an IV rank cannot be computed from Yahoo
data.** `option_chain()` returns the current implied volatility of each
contract and no history whatsoever. A 52-week IV rank needs 252 daily
observations of at-the-money IV, and no free endpoint publishes them. Printing
a number labelled "IV Rank" derived from anything else would be a fabrication
in the single most load-bearing field on the options panel — the one a premium
seller uses to decide whether options are expensive today.

So rather than fake it or block on a vendor, volatility context sits behind a
Protocol, the same way ModelProvider hides five LLM backends behind one env
var. The free implementation ships now and says plainly what it does not know;
a paid implementation is a config change rather than a rewrite.

Both implementations return the **same keys**, always. `iv_rank_available`
tells the caller whether `iv_rank` means anything, so the frontend renders one
label or the other without ever branching on which provider is configured.
There is a unit test asserting the two envelopes match, because the moment
they drift the panel starts reading undefined fields.

Adding a real provider
----------------------
`MarketDataAppProvider` below is the intended next step and is deliberately
left unimplemented. The reason it is the right vendor: its option-chain
endpoint accepts a `date` parameter, so historical ATM implied vol can be
**backfilled in one job** — a genuine 52-week rank on the first day the key is
configured, instead of a year of accumulating snapshots forward.

Doing that properly also needs a table this module does not create:

    option_iv_snapshots(symbol, snapshot_date, dte_bucket, atm_iv, spot, source)
      unique (symbol, snapshot_date, dte_bucket)

as an alembic revision following 002_market_data_store.py, a backfill command
beside `python -m app.db.migrate`, and a daily incremental job. Under the
default provider that table would hold nothing worth querying, which is why it
waits for the vendor rather than landing speculatively.
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache
from typing import Protocol, runtime_checkable

import structlog

from app.config import Settings, get_settings
from app.services import bar_store
from app.services.options_math import percentile_of, realized_vol, rolling_realized_vol

log = structlog.get_logger(__name__)

# A year of realized-vol readings needs a year of returns plus the window that
# produces the first one, and splits mean we want the adjusted series anyway.
LOOKBACK_DAYS = 450
SHORT_VOL_WINDOW = 20
MEDIUM_VOL_WINDOW = 60
LONG_VOL_WINDOW = 252

PROXY_NOTE = (
    "Yahoo publishes no implied-volatility history, so this is not an IV rank. "
    "It shows where today's 30-day at-the-money IV sits inside the last year of "
    "this stock's own 20-day realized volatility readings."
)

RANK_NOTE = (
    "IV rank is the percentile of today's 30-day at-the-money implied "
    "volatility within its own trailing 52 weeks."
)


def empty_context(reason: str | None = None) -> dict:
    """The envelope with everything unknown.

    Returned when there aren't enough bars, or the store is unreachable. Every
    key is present so callers never have to test for absence — only for None.
    """
    return {
        "atm_iv_30d": None,
        "hv_20": None,
        "hv_60": None,
        "hv_252": None,
        "iv_hv_ratio": None,
        "iv_hv_spread": None,
        "hv_percentile_252": None,
        "iv_percentile_vs_realized": None,
        "iv_rank": None,
        "iv_percentile_252": None,
        "iv_rank_available": False,
        "method": "unavailable",
        "note": reason or "Not enough price history to measure volatility.",
    }


@runtime_checkable
class VolatilityProvider(Protocol):
    """Supplies the volatility block on the options panel."""

    name: str

    async def vol_context(self, symbol: str, atm_iv_30d: float | None) -> dict:
        """Return the volatility envelope for one symbol.

        Implementations must return every key `empty_context()` returns, so
        that the caller never branches on which provider is active.
        """
        ...


class RealizedVolProxyProvider:
    """Free, honest, and explicit about the one thing it cannot do.

    Places today's implied volatility inside the distribution of this stock's
    own realized volatility over the past year. That is not an IV rank, and
    `iv_rank_available` is False to say so — but it does answer the question
    that actually drives a premium-selling decision: is the market charging
    more than this stock has been moving, by this stock's own standards?
    """

    name = "realized_proxy"

    async def vol_context(self, symbol: str, atm_iv_30d: float | None) -> dict:
        closes = await self._adjusted_closes(symbol)
        if not closes:
            return empty_context("No stored price history for this symbol.")

        hv_20 = realized_vol(closes, SHORT_VOL_WINDOW)
        hv_60 = realized_vol(closes, MEDIUM_VOL_WINDOW)
        hv_252 = realized_vol(closes, LONG_VOL_WINDOW)

        # The comparison sample: a year of rolling 20-day readings, which is
        # what "how much does this stock usually move" means in practice.
        history = rolling_realized_vol(closes, SHORT_VOL_WINDOW)[-LONG_VOL_WINDOW:]

        context = empty_context()
        context.update(
            {
                "atm_iv_30d": atm_iv_30d,
                "hv_20": hv_20,
                "hv_60": hv_60,
                "hv_252": hv_252,
                "hv_percentile_252": (
                    percentile_of(hv_20, history) if hv_20 is not None and history else None
                ),
                "method": "realized_vol_proxy",
                "note": PROXY_NOTE,
                "iv_rank_available": False,
            }
        )

        if atm_iv_30d is not None:
            context["iv_hv_ratio"] = atm_iv_30d / hv_20 if hv_20 else None
            context["iv_hv_spread"] = atm_iv_30d - hv_20 if hv_20 is not None else None
            context["iv_percentile_vs_realized"] = (
                percentile_of(atm_iv_30d, history) if history else None
            )

        return context

    async def _adjusted_closes(self, symbol: str) -> list[float]:
        """Split-adjusted closes from the local store.

        Adjusted on purpose: a raw series shows a 50% single-day move at every
        split, which would register as a volatility spike that never happened
        and drag the whole percentile distribution with it.
        """
        try:
            rows = await bar_store.daily_bars_for(
                symbol,
                start=date.today() - timedelta(days=LOOKBACK_DAYS),
                adjusted=True,
                span="2y",
            )
        except Exception as e:
            log.warning("vol_history_unavailable", symbol=symbol, error=str(e))
            return []
        return [r.close for r in rows if r.close]


class MarketDataAppProvider:
    """Real IV rank from dated historical chains. Not implemented yet.

    See the module docstring for why this vendor and what else it needs. The
    class exists so the seam is real rather than hypothetical — the factory
    resolves it, the config accepts it, and the only missing piece is the
    vendor call itself.
    """

    name = "marketdata"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def vol_context(self, symbol: str, atm_iv_30d: float | None) -> dict:
        raise NotImplementedError(
            "MarketDataAppProvider needs the option_iv_snapshots table and a "
            "backfill job before it can answer. Use VOL_DATA_PROVIDER=realized_proxy."
        )


def build_vol_provider(settings: Settings) -> VolatilityProvider:
    """Resolve a provider from settings, with no caching.

    Falls back to the free proxy when a paid provider is selected without a
    key, matching how NEWSAPI_KEY and TAVILY_API_KEY degrade — a missing
    optional key should cost you a feature, not the page.
    """
    if settings.vol_data_provider == "marketdata":
        if not settings.marketdata_api_key:
            log.warning(
                "vol_provider_missing_key",
                requested="marketdata",
                falling_back_to="realized_proxy",
            )
            return RealizedVolProxyProvider()
        return MarketDataAppProvider(api_key=settings.marketdata_api_key)

    return RealizedVolProxyProvider()


@lru_cache(maxsize=1)
def _default_provider() -> VolatilityProvider:
    return build_vol_provider(get_settings())


def get_vol_provider(settings: Settings | None = None) -> VolatilityProvider:
    """Return the configured volatility provider.

    The no-argument call — which is every call in application code — is a
    cached singleton. Passing settings explicitly bypasses the cache and
    builds fresh, because Settings is a pydantic model and therefore
    unhashable: decorating this function with lru_cache directly would raise
    TypeError the first time anyone passed one.
    """
    if settings is not None:
        return build_vol_provider(settings)
    return _default_provider()
