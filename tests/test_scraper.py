"""Tests for the scraper fallback behavior."""

from __future__ import annotations

import pytest

from congiuntura_live.scraper import PressReleaseScraper


@pytest.fixture
def scraper():
    return PressReleaseScraper(max_chars=500)


class TestScraper:
    async def test_returns_empty_on_404(self, scraper: PressReleaseScraper):
        """A non-existent URL should return empty string (triggers fallback)."""
        content = await scraper.extract_content("https://www.insee.fr/en/statistiques/0000000")
        assert content == ""

    async def test_returns_empty_on_invalid_url(self, scraper: PressReleaseScraper):
        content = await scraper.extract_content("https://invalid-domain-12345.example/page")
        assert content == ""

    async def test_extracts_from_real_insee_page(self, scraper: PressReleaseScraper):
        """Live test: the INSEE page should return non-empty extracted text."""
        content = await scraper.extract_content("https://www.insee.fr/en/statistiques/9030714")
        assert len(content) > 50
        # Should be cleaned text, not raw HTML
        assert "<html" not in content.lower()

    async def test_respects_max_chars(self):
        """Content should be truncated to max_chars."""
        scraper = PressReleaseScraper(max_chars=100)
        content = await scraper.extract_content("https://www.insee.fr/en/statistiques/9030714")
        if content:  # Only test if extraction succeeded
            assert len(content) <= 110  # Allow for truncation suffix
            assert "…" in content

    async def test_close_does_not_error_when_not_started(self):
        scraper = PressReleaseScraper()
        await scraper.close()  # Should not raise
