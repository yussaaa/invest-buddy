"""Social sentiment providers behind one interface.

    get_provider("apewisdom").trending(filter="wallstreetbets", limit=50)
    await statuses()   # what the Signals page shows as source chips

Every provider method returns a reading with `available` and `caveat` and
never raises — the convention every tool in this app follows, so a dead
source becomes a sentence rather than a 500.
"""

from __future__ import annotations

from typing import Protocol

from app.services.social.apewisdom import ApeWisdomProvider
from app.services.social.models import ProviderStatus
from app.services.social.placeholders import RedditApiProvider, XProvider
from app.services.social.stocktwits import StockTwitsProvider


class SocialProvider(Protocol):
    id: str
    label: str
    kind: str
    capabilities: tuple[str, ...]

    async def status(self) -> ProviderStatus: ...


PROVIDERS: dict[str, SocialProvider] = {
    p.id: p for p in (ApeWisdomProvider(), StockTwitsProvider(), RedditApiProvider(), XProvider())
}


def get_provider(provider_id: str) -> SocialProvider | None:
    return PROVIDERS.get(provider_id)


async def statuses() -> list[ProviderStatus]:
    return [await p.status() for p in PROVIDERS.values()]
