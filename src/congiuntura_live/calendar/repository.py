"""Calendar MongoDB repository — nso_releases and ff_releases collections."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from pymongo import AsyncMongoClient
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.database import AsyncDatabase

logger = logging.getLogger(__name__)

NSO_COLLECTION = "nso_releases"
FF_COLLECTION = "ff_releases"


class CalendarRepository:
    """Repository for NSO and ForexFactory calendar releases."""

    def __init__(self, mongo_url: str, database_name: str) -> None:
        self._client = AsyncMongoClient(mongo_url)
        self._db: AsyncDatabase = self._client[database_name]
        self._nso: AsyncCollection = self._db[NSO_COLLECTION]
        self._ff: AsyncCollection = self._db[FF_COLLECTION]

    async def ensure_indexes(self) -> None:
        """Indexes for dedup (source_uid unique) and calendar queries."""
        for coll in (self._nso, self._ff):
            await coll.create_index("source_uid", unique=True)
            await coll.create_index([("release_dt", -1)])
        await self._nso.create_index("source")
        await self._ff.create_index("impact")
        logger.info("MongoDB indexes ensured on '%s' and '%s'", NSO_COLLECTION, FF_COLLECTION)

    # ── Upserts ────────────────────────────────────────────────

    async def upsert_nso(self, records: list[dict]) -> int:
        """Upsert NSO releases by source_uid. Returns count written."""
        count = 0
        for r in records:
            result = await self._nso.update_one(
                {"source_uid": r["source_uid"]},
                {
                    "$set": {
                        "source": r["source"],
                        "title": r["title"],
                        "release_dt": r["release_dt"],
                        "reference_period": r.get("reference_period"),
                        "url": r.get("url"),
                        "updated_at": datetime.now(UTC),
                    },
                    "$setOnInsert": {"first_seen": datetime.now(UTC)},
                },
                upsert=True,
            )
            if result.upserted_id or result.modified_count:
                count += 1
        if count:
            logger.info("NSO upsert: %d releases", count)
        return count

    async def upsert_ff(self, records: list[dict]) -> int:
        """Upsert ForexFactory releases; updates actual/forecast/previous via
        COALESCE-like semantics (never overwrites with null). History is
        preserved — records outside the scrape window are never touched."""
        count = 0
        for r in records:
            now = datetime.now(UTC)
            set_ops: dict[str, Any] = {
                "source": "forexfactory",
                "title": r["title"],
                "release_dt": r["release_dt"],
                "release_dt_orig": r.get("release_dt_orig"),
                "impact": r["impact"],
                "currency": r.get("currency", "EUR"),
                "updated_at": now,
            }
            # Conditional updates: only set fields that have values
            # (never overwrite an existing actual with null)
            for field in ("actual", "forecast", "previous"):
                if r.get(field):
                    set_ops[field] = r[field]
            update: dict[str, Any] = {
                "$set": set_ops,
                "$setOnInsert": {"first_seen": now},
            }
            result = await self._ff.update_one(
                {"source_uid": r["source_uid"]}, update, upsert=True
            )
            if result.upserted_id or result.modified_count:
                count += 1
        if count:
            logger.info("FF upsert: %d releases", count)
        return count

    # ── Queries ────────────────────────────────────────────────

    async def search_calendar(
        self,
        source: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        q: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Unified search across NSO + FF releases (calendar view)."""
        base_query: dict[str, Any] = {}
        if q:
            base_query["title"] = {"$regex": q, "$options": "i"}
        date_filter: dict[str, Any] = {}
        if date_from:
            date_filter["$gte"] = date_from
        if date_to:
            date_filter["$lte"] = date_to
        if date_filter:
            base_query["release_dt"] = date_filter

        def _nso_query() -> dict[str, Any]:
            query = dict(base_query)
            if source and source != "all":
                # NSO docs carry their own source code (eurostat, istat, ...)
                query["source"] = "__none__" if source == "forexfactory" else source
            return query

        def _ff_query() -> dict[str, Any]:
            query = dict(base_query)
            if source and source != "all":
                query["source"] = "forexfactory" if source == "forexfactory" else "__none__"
            return query

        nso_results = await self._nso.find(_nso_query(), {"_id": 0}).sort("release_dt", 1).to_list(length=limit)
        ff_results = await self._ff.find(_ff_query(), {"_id": 0}).sort("release_dt", 1).to_list(length=limit)

        results = nso_results + ff_results
        results.sort(key=lambda r: r["release_dt"])
        return results[:limit]

    async def list_calendar_sources(self) -> list[dict[str, str]]:
        """Distinct sources present in the calendar collections."""
        nso_sources = await self._nso.distinct("source")
        has_ff = await self._ff.count_documents({}) > 0
        names = {
            "eurostat": "Eurostat", "istat": "Istat", "ine": "INE",
            "destatis": "Destatis", "insee": "INSEE", "cso": "CSO",
            "forexfactory": "ForexFactory",
        }
        sources = [{"code": s, "name": names.get(s, s)} for s in sorted(nso_sources)]
        if has_ff:
            sources.append({"code": "forexfactory", "name": "ForexFactory"})
        return sources

    async def close(self) -> None:
        await self._client.close()
