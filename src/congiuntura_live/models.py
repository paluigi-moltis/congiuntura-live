"""Pydantic models for press releases and feed entries."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(UTC)


def compute_url_hash(url: str) -> str:
    """Return SHA-256 hex digest of a URL string (used for deduplication)."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


class FeedEntry(BaseModel):
    """A single item parsed from an RSS/Atom feed, before enrichment."""

    title: str
    url: str
    summary: str = ""
    published: datetime | None = None
    tags: list[str] = Field(default_factory=list)

    @property
    def url_hash(self) -> str:
        return compute_url_hash(self.url)


class PressRelease(BaseModel):
    """A press release as stored in MongoDB and served to the web UI."""

    url_hash: str
    url: str
    title: str
    summary: str = ""
    publisher: str  # agency slug (e.g. "istat")
    publisher_full: str  # display name (e.g. "Istat")
    feed_label: str  # which feed within the agency
    language: str
    published: datetime | None = None
    fetched_at: datetime = Field(default_factory=_utcnow)
    tags: list[str] = Field(default_factory=list)

    def to_doc(self) -> dict[str, Any]:
        """Convert to a MongoDB document dict (alias-aware)."""
        return self.model_dump(mode="json")
