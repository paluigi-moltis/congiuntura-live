"""Title translation backfill for already-processed releases.

Runs once at startup: finds processed documents missing the `title_en`
field and translates their titles with a single lightweight LLM call
per item (no page re-scraping, no full re-extraction).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from outlines_cascade import generate, load_config
from outlines_cascade.config import AppConfig as CascadeAppConfig
from pydantic import BaseModel, Field

from .repository import PressReleaseRepository
from .settings import ProcessingConfig, load_extraction_model

logger = logging.getLogger(__name__)

_TRANSLATE_SYSTEM_PROMPT = (
    "You are a professional translator for official European statistics "
    "press releases. Translate the given title into English. Keep official "
    "statistical terminology (e.g. HICP, industrial production, flash "
    "estimate). If the title is already in English, reproduce it verbatim. "
    "Output only the translation."
)


class _TitleTranslation(BaseModel):
    """Minimal structured output for title-only translation."""

    title_en: str = Field(description="English translation of the title")


class TitleBackfiller:
    """Backfills the `title_en` field on processed releases missing it."""

    def __init__(self, processing_cfg: ProcessingConfig, repo: PressReleaseRepository) -> None:
        self._cfg = processing_cfg
        self._repo = repo
        self._cascade_config: CascadeAppConfig | None = None

    def _ensure_config(self) -> CascadeAppConfig:
        if self._cascade_config is None:
            self._cascade_config = load_config(self._cfg.llm_config)
        return self._cascade_config

    async def find_missing_title_en(self, limit: int = 500) -> list[dict[str, Any]]:
        """Processed documents lacking the title_en field."""
        return await self._repo.find_processed_missing_field("title_en", limit=limit)

    async def backfill_once(self, batch_size: int = 50) -> int:
        """Translate one batch of pending titles. Returns count updated."""
        pending = await self.find_missing_title_en(limit=batch_size)
        if not pending:
            return 0

        updated = 0
        for doc in pending:
            url_hash = doc.get("url_hash")
            title = doc.get("title", "")
            if not title:
                continue

            try:
                result = await generate(
                    prompt=f"Title: {title}",
                    output_type=_TitleTranslation,
                    config=self._ensure_config(),
                    cascade_name=self._cfg.cascade_name,
                    system_prompt=_TRANSLATE_SYSTEM_PROMPT,
                )
                title_en = result.value.title_en.strip()
                if title_en:
                    await self._repo.set_processed_field(
                        url_hash, "title_en", title_en
                    )
                    updated += 1
            except Exception:
                logger.exception("Title backfill failed for %s", url_hash)

        if updated:
            logger.info("Title backfill: %d titles translated", updated)
        return updated

    async def backfill_all(self, batch_size: int = 50) -> int:
        """Translate ALL pending titles in batches. Runs at startup."""
        total = 0
        while True:
            n = await self.backfill_once(batch_size=batch_size)
            total += n
            if n < batch_size:
                break
        if total:
            logger.info("Title backfill complete: %d titles translated", total)
        else:
            logger.info("Title backfill: nothing to do (all titles already translated)")
        return total


async def run_title_backfill_on_startup(
    processing_cfg: ProcessingConfig,
    repo: PressReleaseRepository,
) -> int:
    """Entry point called from app lifespan. Failures are logged, never fatal."""
    try:
        backfiller = TitleBackfiller(processing_cfg, repo)
        return await backfiller.backfill_all()
    except Exception:
        logger.exception("Title backfill startup task failed")
        return 0
