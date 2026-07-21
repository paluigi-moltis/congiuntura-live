"""Async MongoDB repository using motor."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection, AsyncIOMotorDatabase

from .models import PressRelease

logger = logging.getLogger(__name__)

COLLECTION = "press_releases"


class PressReleaseRepository:
    """Asynchronous repository for press releases in MongoDB.

    Implements an async OOP repository pattern over motor.
    """

    def __init__(self, mongo_url: str, database_name: str) -> None:
        self._client = AsyncIOMotorClient(mongo_url)
        self._db: AsyncIOMotorDatabase = self._client[database_name]
        self._collection: AsyncIOMotorCollection = self._db[COLLECTION]

    async def ensure_indexes(self) -> None:
        """Create indexes for dedup and query performance."""
        await self._collection.create_index("url_hash", unique=True)
        await self._collection.create_index("publisher")
        await self._collection.create_index([("published", -1)])
        await self._collection.create_index([("publisher", "published")])
        logger.info("MongoDB indexes ensured on '%s'", COLLECTION)

    async def insert_many_new(self, releases: list[PressRelease]) -> tuple[int, int]:
        """Insert releases, skipping duplicates by url_hash.

        Returns (inserted_count, skipped_count).
        """
        inserted = 0
        skipped = 0
        for release in releases:
            doc = release.to_doc()
            try:
                await self._collection.insert_one(doc)
                inserted += 1
            except Exception:  # DuplicateKeyError from unique index
                skipped += 1
        if inserted or skipped:
            logger.info("Insert: %d new, %d duplicates skipped", inserted, skipped)
        return inserted, skipped

    async def search(
        self,
        publisher: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Search press releases by publisher and date range."""
        query: dict[str, Any] = {}
        if publisher and publisher != "all":
            query["publisher"] = publisher
        date_filter: dict[str, Any] = {}
        if date_from:
            date_filter["$gte"] = date_from
        if date_to:
            date_filter["$lte"] = date_to
        if date_filter:
            query["published"] = date_filter

        cursor = self._collection.find(query, {"_id": 0}).sort("published", -1).limit(limit)
        return await cursor.to_list(length=limit)

    async def count(
        self,
        publisher: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> int:
        """Count documents matching the search criteria."""
        query: dict[str, Any] = {}
        if publisher and publisher != "all":
            query["publisher"] = publisher
        date_filter: dict[str, Any] = {}
        if date_from:
            date_filter["$gte"] = date_from
        if date_to:
            date_filter["$lte"] = date_to
        if date_filter:
            query["published"] = date_filter
        return await self._collection.count_documents(query)

    async def count_total(self) -> int:
        """Total document count (for dashboard stats)."""
        return await self._collection.count_documents({})

    async def list_publishers(self) -> list[str]:
        """Return distinct publisher slugs present in the database."""
        return await self._collection.distinct("publisher")

    async def close(self) -> None:
        self._client.close()
