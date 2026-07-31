"""Async scraper for press release page content using trafilatura."""

from __future__ import annotations

import logging

import httpx
import trafilatura

logger = logging.getLogger(__name__)

_SCRAPER_USER_AGENT = "congiuntura-live/0.1 (+https://github.com/paluigi/congiuntura-live)"


class PressReleaseScraper:
    """Fetches and extracts main text content from press release pages.

    Uses httpx for async HTTP and trafilatura for boilerplate-aware text
    extraction (handles navigation bars, scripts, ads, etc.).
    """

    def __init__(self, timeout: float = 30.0, max_chars: int = 4000) -> None:
        self._timeout = timeout
        self._max_chars = max_chars
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                headers={"User-Agent": _SCRAPER_USER_AGENT},
                follow_redirects=True,
            )
        return self._client

    async def extract_content(self, url: str) -> str:
        """Fetch the page at ``url`` and return cleaned main text.

        Returns empty string if the page cannot be fetched or parsed.
        """
        try:
            client = await self._get_client()
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
        except httpx.HTTPError as exc:
            logger.warning("Scrape HTTP error for %s: %s", url, exc)
            return ""

        # trafilatura extracts the main article text, removing boilerplate.
        extracted = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
        )
        if not extracted:
            logger.debug("trafilatura extracted nothing from %s", url)
            return ""

        # Truncate to avoid token explosion in the LLM prompt.
        if len(extracted) > self._max_chars:
            extracted = extracted[: self._max_chars] + " […]"
        return extracted

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
