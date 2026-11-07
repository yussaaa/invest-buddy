"""What every news source shares: a status chip that tells the truth.

A source is `not_configured` when it needs a key or contact we don't have,
`blocked` when its last attempt was refused, and `live` otherwise. The page
shows this up front, so a quiet wire never reads as a quiet market.
"""

from __future__ import annotations

from typing import Optional

from app.config import get_settings
from app.services.news.models import ProviderStatus


class NewsProvider:
    id: str
    label: str
    capabilities: tuple[str, ...] = ()

    def __init__(self) -> None:
        self.last_error: Optional[str] = None

    def missing_config(self) -> Optional[str]:
        """Why this source cannot run, or None if it can."""
        return None

    def status(self) -> ProviderStatus:
        enabled = get_settings().news_enabled
        missing = self.missing_config()
        if not enabled:
            state, reason = "disabled", "The news wire is off (NEWS_ENABLED=false)."
        elif missing:
            state, reason = "not_configured", missing
        elif self.last_error:
            state, reason = "blocked", self.last_error
        else:
            state, reason = "live", None
        return ProviderStatus(
            id=self.id, label=self.label, kind="live",
            capabilities=list(self.capabilities),
            enabled=enabled and not missing, state=state, reason=reason,
        )
