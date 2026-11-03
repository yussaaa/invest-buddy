"""Reddit API and X — declared, not yet implemented.

They exist so the interface is real before the integrations are: the Signals
page lists them, says exactly what is missing, and nothing has to change
shape when one is built. ApeWisdom already covers Reddit mention counts
without credentials; X's read API is paid.

No `praw` or `tweepy` import here, on purpose — the dependency arrives with
the implementation, not before (the .venv is shared across worktrees).
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.config import get_settings
from app.services.social.models import ProviderStatus, Reading, TrendingResult


class _Placeholder:
    id: str
    label: str
    kind = "placeholder"
    capabilities = ("trending", "ticker")
    env: str
    alternative: str

    def _configured(self) -> bool:
        raise NotImplementedError

    def _reason(self) -> str:
        if self._configured():
            return f"Credentials found, but the {self.label} provider is not implemented yet."
        return f"{self.env} not set. {self.alternative}"

    async def status(self) -> ProviderStatus:
        return ProviderStatus(
            id=self.id, label=self.label, kind="placeholder",
            capabilities=list(self.capabilities), enabled=False,
            state="not_configured", reason=self._reason(),
        )

    async def trending(self, filter: str = "", limit: int = 50) -> TrendingResult:
        return TrendingResult(source=self.id, filter=filter, as_of=datetime.now(timezone.utc),
                              available=False, caveat=self._reason())

    async def ticker(self, symbol: str) -> Reading:
        return Reading(source=self.id, available=False, caveat=self._reason())


class RedditApiProvider(_Placeholder):
    id = "reddit"
    label = "Reddit API"
    env = "REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET"
    alternative = "ApeWisdom already covers Reddit mention counts without a key."

    def _configured(self) -> bool:
        s = get_settings()
        return bool(s.reddit_client_id and s.reddit_client_secret)


class XProvider(_Placeholder):
    id = "x"
    label = "X / Twitter"
    env = "X_BEARER_TOKEN"
    alternative = "X's read API is a paid tier."

    def _configured(self) -> bool:
        return bool(get_settings().x_bearer_token)
