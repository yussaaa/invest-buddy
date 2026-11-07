"""The market news wire's sources behind one interface.

    FED.poll()                    # one poll → Batch
    statuses()                    # what the News page shows as source chips

Like services/social/, every provider returns a reading with `available` and
`caveat` and never raises from a poll, so a dead source becomes a sentence on
the page rather than a hole in the wire.
"""

from __future__ import annotations

from app.services.news.alpaca import AlpacaProvider
from app.services.news.base import NewsProvider
from app.services.news.edgar import EdgarProvider
from app.services.news.fed import FedProvider
from app.services.news.models import ProviderStatus

# Upper-case so they never shadow the submodules of the same name.
ALPACA = AlpacaProvider()
EDGAR = EdgarProvider()
FED = FedProvider()

PROVIDERS: dict[str, NewsProvider] = {p.id: p for p in (ALPACA, EDGAR, FED)}


def statuses() -> list[ProviderStatus]:
    return [p.status() for p in PROVIDERS.values()]
