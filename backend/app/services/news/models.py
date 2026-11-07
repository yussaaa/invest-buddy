"""Shapes shared by every news source.

A `Headline` is what ingest knows: who said what, when, about which tickers.
Interpretation — importance, exposure, explanation — is added by later stages
and never by a provider, so a provider stays a parser that can be tested
against a recorded payload.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.services.social.models import ProviderStatus  # noqa: F401 — same chip shape

NewsSource = Literal["alpaca", "edgar", "fed"]

# Ingest assigns these from structure (an 8-K item number, a Fed release
# category), never from reading the text — that is triage's job.
Category = Literal[
    "earnings", "deal", "management", "accounting", "corporate",
    "monetary_policy", "regulatory", "news",
]
CATEGORIES: tuple[str, ...] = Category.__args__  # type: ignore[attr-defined]


def headline_id(source: str, source_id: str) -> str:
    """Stable across polls and restarts, so storing a headline is idempotent."""
    return hashlib.sha1(f"{source}:{source_id}".encode()).hexdigest()[:24]


class Headline(BaseModel):
    id: str
    source: NewsSource
    source_id: str
    publisher: Optional[str] = None
    title: str
    summary: Optional[str] = None
    url: Optional[str] = None
    published_at: datetime
    symbols: list[str] = Field(default_factory=list)
    category: Optional[Category] = None
    tags: list[str] = Field(default_factory=list)


class Batch(BaseModel):
    """One poll of one source."""

    source: NewsSource
    available: bool = True
    caveat: Optional[str] = None
    headlines: list[Headline] = Field(default_factory=list)
