"""Request and output shapes for the research agent."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.agents.base.schemas import GuardrailFlag

SourceKind = Literal["news", "web", "filing", "social", "market_data"]

RESEARCH_DISCLAIMER = (
    "Assembled from news, web search and online discussion, and checked against "
    "those sources. It describes why a ticker is being talked about; it is not a "
    "view on what to do about it."
)


class ResearchRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=16)
    question: Optional[str] = Field(None, max_length=300)


class ResearchSource(BaseModel):
    """One thing the brief may cite. Built from tool payloads, never by the model."""

    id: str                         # "S1", "S2" …
    kind: SourceKind
    title: str
    url: Optional[str] = None
    publisher: Optional[str] = None
    published_at: Optional[str] = None
    tool: str


class ResearchFinding(BaseModel):
    text: str
    source_ids: list[str] = Field(min_length=1)


class ResearchBrief(BaseModel):
    ticker: str
    question: str
    as_of: datetime
    headline: str
    drivers: list[ResearchFinding] = Field(default_factory=list)
    crowd_read: str = ""
    what_would_change: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    sources: list[ResearchSource] = Field(default_factory=list)
    social_signal_set: Optional[dict] = None
    markdown: str = ""
    flags: list[GuardrailFlag] = Field(default_factory=list)
    guarded: bool = False
    model: str = ""
    disclaimer: str = RESEARCH_DISCLAIMER
