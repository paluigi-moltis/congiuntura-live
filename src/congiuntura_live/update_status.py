"""Update-status tracker — helper MongoDB collection.

Records when each pipeline (press releases, calendar) last ran, so the
web pages can display a 'Last update' indicator. One document per
pipeline key::

    { "_id": "press_releases", "last_run": <naive UTC dt>,
      "status": "ok"|"error", "details": "5 new releases" }
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from pymongo import AsyncMongoClient
from pymongo.asynchronous.collection import AsyncCollection

logger = logging.getLogger(__name__)

STATUS_COLLECTION = "update_status"

KEY_PRESSES = "press_releases"
KEY_CALENDAR = "calendar"


class UpdateStatusRepository:
    """Tiny repository for pipeline last-run timestamps."""

    def __init__(self, mongo_url: str, database_name: str) -> None:
        self._client = AsyncMongoClient(mongo_url)
        self._db = self._client[database_name]
        self._coll: AsyncCollection = self._db[STATUS_COLLECTION]

    async def ensure_indexes(self) -> None:
        await self._coll.create_index("_id")

    async def mark(self, key: str, status: str = "ok", details: str = "") -> None:
        """Record a completed (or failed) run for `key`."""
        await self._coll.update_one(
            {"_id": key},
            {
                "$set": {
                    "last_run": datetime.now(UTC).replace(tzinfo=None),
                    "status": status,
                    "details": details,
                }
            },
            upsert=True,
        )

    async def get(self, key: str) -> dict[str, Any] | None:
        return await self._coll.find_one({"_id": key})

    async def get_all(self) -> dict[str, dict[str, Any]]:
        """All status documents keyed by pipeline key."""
        docs = await self._coll.find({}).to_list(length=None)
        return {d["_id"]: d for d in docs}

    async def close(self) -> None:
        await self._client.close()
