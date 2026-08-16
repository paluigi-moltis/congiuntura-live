"""Calendar poller — daily collection at 07:00 UTC via APScheduler cron."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .collectors import NSO_COLLECTORS, ForexFactoryCollector
from .config import CalendarConfig
from .repository import CalendarRepository

logger = logging.getLogger(__name__)


class CalendarPoller:
    """Runs all calendar collectors on a daily cron schedule.

    NSO collectors refresh ICS feeds / HTML calendars; the ForexFactory
    collector re-scrapes (current month − 1) to (current month + 3) to
    pick up actuals and extend the forward horizon. History beyond the
    scrape window is never touched.
    """

    def __init__(self, repo: CalendarRepository, config: CalendarConfig | None = None,
                 status_repo=None, on_update=None) -> None:
        self._repo = repo
        self._config = config or CalendarConfig()
        self._status_repo = status_repo
        self._on_update = on_update
        self._scheduler = AsyncIOScheduler()

    async def collect_once(self) -> dict[str, int]:
        """Run all collectors once. Returns per-source counts."""
        counts: dict[str, int] = {}
        errors = 0

        for collector_cls in NSO_COLLECTORS:
            collector = collector_cls()
            try:
                records = await collector.collect()
                counts[collector.source_code()] = await self._repo.upsert_nso(records)
            except Exception:
                logger.exception("Calendar collector %s failed", collector.source_code())
                errors += 1

        try:
            ff = ForexFactoryCollector()
            records = await ff.collect_routine()
            counts["forexfactory"] = await self._repo.upsert_ff(records)
        except Exception:
            logger.exception("ForexFactory collector failed")
            errors += 1

        logger.info("Calendar collection cycle complete: %s", counts)

        # Record last-run status and push to connected clients
        if self._status_repo is not None:
            try:
                total = sum(counts.values())
                await self._status_repo.mark(
                    "calendar",
                    status="ok" if errors == 0 else "partial",
                    details=f"{total} releases"
                    + (f", {errors} collector errors" if errors else ""),
                )
            except Exception:
                logger.exception("Failed to mark calendar status")
        if self._on_update is not None:
            try:
                self._on_update()
            except Exception:
                logger.exception("on_update callback failed")
        return counts

    async def backfill_ff(self) -> int:
        """One-time ForexFactory backfill from BACKFILL_START."""
        ff = ForexFactoryCollector()
        records = await ff.collect_initial_backfill()
        return await self._repo.upsert_ff(records)

    def start(self) -> None:
        """Start the daily cron job at 07:00 UTC (configurable)."""
        if not self._config.polling.enabled:
            logger.info("Calendar polling disabled via config")
            return
        self._scheduler.add_job(
            self.collect_once,
            trigger=CronTrigger(
                hour=self._config.polling.cron_hour,
                minute=self._config.polling.cron_minute,
                timezone="UTC",
            ),
            id="calendar_collect",
            replace_existing=True,
        )
        self._scheduler.start()
        logger.info(
            "Calendar scheduler started — daily at %02d:%02d UTC",
            self._config.polling.cron_hour,
            self._config.polling.cron_minute,
        )

    def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
