"""Tests for deduplication logic (url_hash based)."""

from __future__ import annotations

import pytest

from congiuntura_live.models import PressRelease, compute_url_hash


class TestUrlHash:
    """Tests for URL hashing and dedup key generation."""

    def test_hash_is_deterministic(self):
        url = "https://www.istat.it/communicato/pil-2025q1"
        assert compute_url_hash(url) == compute_url_hash(url)

    def test_different_urls_different_hash(self):
        assert compute_url_hash("https://a.com/1") != compute_url_hash("https://a.com/2")

    def test_hash_is_sha256_hex(self):
        h = compute_url_hash("https://example.com")
        assert len(h) == 64  # SHA-256 hex digest
        assert all(c in "0123456789abcdef" for c in h)


class TestDedupViaRepository:
    """Tests for deduplication behavior at the repository level.

    Uses a mock collection to simulate MongoDB's unique index on url_hash.
    """

    class MockCollection:
        """Minimal mock of motor's AsyncIOMotorCollection for dedup testing."""

        def __init__(self):
            self._docs: dict[str, dict] = {}

        async def insert_one(self, doc: dict):
            key = doc["url_hash"]
            if key in self._docs:
                raise Exception("DuplicateKeyError")
            self._docs[key] = doc

        async def count_documents(self, query: dict | None = None) -> int:
            return len(self._docs)

    @pytest.fixture
    def mock_repo(self):
        from congiuntura_live.repository import PressReleaseRepository

        repo = PressReleaseRepository.__new__(PressReleaseRepository)
        repo._collection = self.MockCollection()
        return repo

    def _make_release(self, url: str, title: str = "Test") -> PressRelease:
        return PressRelease(
            url_hash=compute_url_hash(url),
            url=url,
            title=title,
            publisher="istat",
            publisher_full="Istat",
            feed_label="National accounts",
            language="it",
        )

    async def test_insert_new_release(self, mock_repo):
        releases = [self._make_release("https://example.com/1")]
        inserted, skipped = await mock_repo.insert_many_new(releases)
        assert inserted == 1
        assert skipped == 0

    async def test_duplicate_url_skipped(self, mock_repo):
        url = "https://www.istat.it/communicato/pil-2025q1"
        releases = [
            self._make_release(url, "First"),
            self._make_release(url, "Duplicate"),  # same URL → same hash
        ]
        inserted, skipped = await mock_repo.insert_many_new(releases)
        assert inserted == 1
        assert skipped == 1

    async def test_cross_feed_duplicate(self, mock_repo):
        """Same press release appearing in two feeds (same URL) is deduplicated."""
        url = "https://www.istat.it/communicato/shared"
        r1 = self._make_release(url, "Shared")
        r1.feed_label = "National accounts"
        r2 = self._make_release(url, "Shared")
        r2.feed_label = "Prices"

        inserted, skipped = await mock_repo.insert_many_new([r1, r2])
        assert inserted == 1
        assert skipped == 1
