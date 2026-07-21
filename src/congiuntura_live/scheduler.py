"""Periodic feed polling scheduler using APScheduler."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .feed_reader import FeedReader
from .models import PressRelease
from .repository import PressReleaseRepository
from .settings import load_feeds_config

logger = logging.getLogger(__name__)


class FeedPoller:
    """Orchestrates periodic polling of all configured feeds.

    Fetches all agency feeds, converts entries to PressRelease objects,
    deduplicates via the repository's unique index on url_hash, and
    inserts new releases into MongoDB.
    """

    def __init__(
        self,
        reader: FeedReader,
        repo: PressReleaseRepository,
        feeds_config_path: str = "config/feeds.toml",
    ) -> None:
        self._reader = reader
        self._repo = repo
        self._feeds_config_path = feeds_config_path
        self._scheduler = AsyncIOScheduler()

    async def poll_once(self) -> int:
        """Run a single poll cycle across all agencies. Returns count of new inserts."""
        agencies = load_feeds_config(self._feeds_config_path)
        total_new = 0

        for slug, agency in agencies.items():
            try:
                fetched = await self._reader.fetch_agency(slug, agency)
            except Exception:
                logger.exception("Error fetching agency %s", slug)
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
        return total_new

    def start(self, interval_minutes: int) -> None:
        """Start the scheduler with the given poll interval."""
        self._scheduler.add_job(
            self.poll_once,
            trigger=IntervalTrigger(minutes=interval_minutes),
            id="poll_feeds",
            replace_existing=True,
            next_run_time=datetime.now(timezone.utc),  # run immediately on startup
        )
        self._scheduler.start()
        logger.info("Scheduler started — polling every %d minutes", interval_minutes)

    async def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        await self._reader.close()
