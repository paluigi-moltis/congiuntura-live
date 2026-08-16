"""Periodic feed polling and processing using APScheduler."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .feed_reader import FeedReader
from .models import PressRelease
from .repository import PressReleaseRepository
from .settings import load_feeds_config

logger = logging.getLogger(__name__)


class FeedPoller:
    """Orchestrates periodic polling of all configured RSS feeds.

    Fetches all agency feeds, converts entries to PressRelease objects,
    deduplicates via the repository's unique index on url_hash, and
    inserts new releases into MongoDB.
    """

    def __init__(
        self,
        reader: FeedReader,
        repo: PressReleaseRepository,
        feeds_config_path: str = "config/feeds.toml",
        status_repo=None,
        on_update=None,
    ) -> None:
        self._reader = reader
        self._repo = repo
        self._feeds_config_path = feeds_config_path
        self._status_repo = status_repo
        self._on_update = on_update
        self._scheduler = AsyncIOScheduler()

    async def poll_once(self) -> int:
        """Run a single poll cycle across all agencies. Returns count of new inserts."""
        agencies = load_feeds_config(self._feeds_config_path)
        total_new = 0
        errors = 0

        for slug, agency in agencies.items():
            try:
                fetched = await self._reader.fetch_agency(slug, agency)
            except Exception:
                logger.exception("Error fetching agency %s", slug)
                errors += 1
                continue

            releases: list[PressRelease] = []
            for feed_cfg, entry in fetched:
                releases.append(
                    PressRelease(
                        url_hash=entry.url_hash,
                        url=entry.url,
                        title=entry.title,
                        summary=entry.summary,
                        publisher=slug,
                        publisher_full=agency.name,
                        feed_label=feed_cfg.label,
                        language=agency.language,
                        published=entry.published,
                        tags=entry.tags,
                    )
                )

            inserted, _ = await self._repo.insert_many_new(releases)
            total_new += inserted

        logger.info("Poll cycle complete: %d new releases", total_new)

        # Record last-run status and push to connected clients
        if self._status_repo is not None:
            try:
                await self._status_repo.mark(
                    "press_releases",
                    status="ok" if errors == 0 else "partial",
                    details=f"{total_new} new releases"
                    + (f", {errors} agency errors" if errors else ""),
                )
            except Exception:
                logger.exception("Failed to mark press-release status")
        if self._on_update is not None:
            try:
                self._on_update()
            except Exception:
                logger.exception("on_update callback failed")
        return total_new

    def start(self, interval_minutes: int) -> None:
        """Start the scheduler with the given poll interval."""
        self._scheduler.add_job(
            self.poll_once,
            trigger=IntervalTrigger(minutes=interval_minutes),
            id="poll_feeds",
            replace_existing=True,
            next_run_time=datetime.now(timezone.utc),
        )
        self._scheduler.start()
        logger.info("Feed scheduler started — polling every %d minutes", interval_minutes)

    async def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        await self._reader.close()


class ProcessingPoller:
    """Orchestrates periodic LLM processing of unprocessed raw releases.

    Queries the raw collection for items not yet in the processed
    collection, scrapes their pages, runs outlines-cascade extraction,
    and inserts the results.
    """

    def __init__(
        self,
        processor,
        repo: PressReleaseRepository,
        interval_minutes: int = 2,
        batch_size: int = 20,
        on_processing_change: Callable[[bool], None] | None = None,
    ) -> None:
        self._processor = processor
        self._repo = repo
        self._interval = interval_minutes
        self._batch_size = batch_size
        self._on_processing_change = on_processing_change
        self._scheduler = AsyncIOScheduler()

    def _signal_processing(self, active: bool) -> None:
        """Notify the UI layer that processing started/stopped."""
        if self._on_processing_change is not None:
            self._on_processing_change(active)

    async def process_once(self) -> int:
        """Process a batch of unprocessed releases. Returns count processed."""
        unprocessed = await self._repo.find_unprocessed(limit=self._batch_size)
        if not unprocessed:
            return 0

        self._signal_processing(True)
        try:
            processed_count = 0
            for raw_doc in unprocessed:
                try:
                    result = await self._processor.process_one(raw_doc)
                    if result:
                        inserted = await self._repo.insert_processed(result)
                        if inserted:
                            processed_count += 1
                except Exception:
                    logger.exception("Error processing %s", raw_doc.get("url_hash", "unknown"))

            logger.info("Processing cycle: %d releases processed", processed_count)
            return processed_count
        finally:
            self._signal_processing(False)

    async def process_all_pending(self) -> int:
        """Process ALL pending items (used by the manual 'Reprocess' button)."""
        self._signal_processing(True)
        try:
            total = 0
            while True:
                batch = await self._repo.find_unprocessed(limit=self._batch_size)
                if not batch:
                    break
                for raw_doc in batch:
                    try:
                        result = await self._processor.process_one(raw_doc)
                        if result:
                            inserted = await self._repo.insert_processed(result)
                            if inserted:
                                total += 1
                    except Exception:
                        logger.exception("Error processing %s", raw_doc.get("url_hash", "unknown"))
            logger.info("Batch processing: %d releases processed", total)
            return total
        finally:
            self._signal_processing(False)

    def start(self) -> None:
        """Start the periodic processing scheduler."""
        self._scheduler.add_job(
            self.process_once,
            trigger=IntervalTrigger(minutes=self._interval),
            id="process_releases",
            replace_existing=True,
        )
        self._scheduler.start()
        logger.info(
            "Processing scheduler started — every %d minutes, batch %d",
            self._interval,
            self._batch_size,
        )

    async def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        await self._processor.close()
