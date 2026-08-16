"""Async RSS/Atom feed reader with feedparser + httpx."""

from __future__ import annotations

import html as html_mod
import logging
import re
import time
from datetime import UTC, datetime

import feedparser
import httpx
from dateutil import parser as dateparser

from .models import FeedEntry
from .settings import AgencyConfig, FeedConfig

logger = logging.getLogger(__name__)

_FEEDPARSER_USER_AGENT = "congiuntura-live/0.1 (+https://github.com/paluigi/congiuntura-live)"

# Month name → number mapping for date extraction from titles (e.g. INE feeds
# embed dates like "21 Julio 26" in the title rather than using <pubDate>).
_MONTH_NAMES: dict[str, dict[str, int]] = {
    "es": {
        "enero": 1,
        "febrero": 2,
        "marzo": 3,
        "abril": 4,
        "mayo": 5,
        "junio": 6,
        "julio": 7,
        "agosto": 8,
        "septiembre": 9,
        "octubre": 10,
        "noviembre": 11,
        "diciembre": 12,
    },
    "de": {
        "januar": 1,
        "februar": 2,
        "märz": 3,
        "april": 4,
        "mai": 5,
        "juni": 6,
        "juli": 7,
        "august": 8,
        "september": 9,
        "oktober": 10,
        "november": 11,
        "dezember": 12,
    },
    "it": {
        "gennaio": 1,
        "febbraio": 2,
        "marzo": 3,
        "aprile": 4,
        "maggio": 5,
        "giugno": 6,
        "luglio": 7,
        "agosto": 8,
        "settembre": 9,
        "ottobre": 10,
        "novembre": 11,
        "dicembre": 12,
    },
    "fr": {
        "janvier": 1,
        "février": 2,
        "mars": 3,
        "avril": 4,
        "mai": 5,
        "juin": 6,
        "juillet": 7,
        "août": 8,
        "septembre": 9,
        "octobre": 10,
        "novembre": 11,
        "décembre": 12,
    },
    "en": {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    },
}


class FeedReader:
    """Fetches and normalizes RSS/Atom feeds into FeedEntry objects.

    Uses httpx for async HTTP and feedparser for XML parsing.
    """

    def __init__(self, timeout: float = 30.0) -> None:
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                headers={"User-Agent": _FEEDPARSER_USER_AGENT},
                follow_redirects=True,
            )
        return self._client

    async def fetch_raw(self, url: str) -> str:
        """Fetch the raw XML body of a feed URL."""
        client = await self._get_client()
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text

    async def fetch_feed(self, feed: FeedConfig) -> list[FeedEntry]:
        """Fetch and parse a single feed, returning normalized entries."""
        t0 = time.monotonic()
        try:
            raw = await self.fetch_raw(feed.url)
        except httpx.HTTPError as exc:
            logger.warning("HTTP error fetching %s: %s", feed.url, exc)
            return []

        parsed = feedparser.parse(raw)
        if parsed.bozo and parsed.bozo_exception:
            logger.debug("Feed %s has parse warning: %s", feed.url, parsed.bozo_exception)

        entries: list[FeedEntry] = []
        for item in parsed.entries:
            entry = self._parse_entry(item, feed)
            if entry:
                entries.append(entry)

        logger.info(
            "Fetched %d entries from %s (%.1fs)",
            len(entries),
            feed.label,
            time.monotonic() - t0,
        )
        return entries

    def _parse_entry(self, item, feed: FeedConfig) -> FeedEntry | None:
        """Parse a feedparser entry into a FeedEntry."""
        url = self._extract_url(item)
        if not url:
            logger.debug("Entry without URL in feed %s, skipping", feed.label)
            return None

        title_raw = item.get("title", "")
        title = self._clean_text(title_raw) or "(untitled)"
        summary = self._clean_text(item.get("summary", "") or item.get("description", ""))
        published = self._parse_date(item)
        # Fallback: some feeds (e.g. INE) embed the date in the title
        if published is None:
            published = self._extract_date_from_title(title_raw)

        tags: list[str] = []
        if hasattr(item, "tags"):
            tags = [t.term for t in item.tags if t.term]

        return FeedEntry(
            title=title,
            url=url,
            summary=summary,
            published=published,
            tags=tags,
        )

    def _extract_url(self, item) -> str | None:
        """Extract the canonical link from an entry (RSS or Atom)."""
        if hasattr(item, "links"):
            for link in item.links:
                if link.get("rel") in (None, "alternate") and link.get("href"):
                    return link["href"]
        link = item.get("link")
        if link:
            return link
        return None

    def _parse_date(self, item) -> datetime | None:
        """Parse the publication date from standard feed fields."""
        for field in ("published_parsed", "updated_parsed"):
            tp = item.get(field)
            if tp:
                try:
                    return datetime(*tp[:6], tzinfo=UTC)
                except (TypeError, ValueError):
                    pass
        for field in ("published", "updated", "created"):
            raw = item.get(field)
            if raw:
                try:
                    dt = dateparser.parse(str(raw))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=UTC)
                    return dt
                except (ValueError, OverflowError):
                    pass
        return None

    @staticmethod
    def _clean_text(text: str) -> str:
        """Strip whitespace and unescape HTML entities (e.g. &nbsp;)."""
        unescaped = html_mod.unescape(text)
        return re.sub(r"\s+", " ", unescaped).strip()

    @staticmethod
    def _extract_date_from_title(title: str) -> datetime | None:
        """Extract a date embedded in a title (e.g. INE: '21 Julio 26')."""
        decoded = html_mod.unescape(title)
        for months in _MONTH_NAMES.values():
            for month_name, month_num in months.items():
                pattern = rf"(\d{{1,2}})\s*{month_name}\.?\s*(\d{{2,4}})"
                m = re.search(pattern, decoded, re.IGNORECASE)
                if m:
                    day = int(m.group(1))
                    year = int(m.group(2))
                    if year < 100:
                        year += 2000
                    try:
                        return datetime(year, month_num, day, tzinfo=UTC)
                    except ValueError:
                        pass
        return None

    async def fetch_agency(
        self, agency_slug: str, agency: AgencyConfig
    ) -> list[tuple[FeedConfig, FeedEntry]]:
        """Fetch all feeds for a given agency.

        Returns a list of (feed_config, entry) tuples so callers know
        which feed each entry came from.
        """
        all_entries: list[tuple[FeedConfig, FeedEntry]] = []
        for feed in agency.feeds:
            entries = await self.fetch_feed(feed)
            all_entries.extend((feed, e) for e in entries)
        logger.info("Agency %s: %d total entries", agency_slug, len(all_entries))
        return all_entries

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
