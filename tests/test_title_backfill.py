"""Tests for the title translation backfill."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from congiuntura_live.title_backfill import TitleBackfiller, _TitleTranslation


def _make_backfiller(repo: MagicMock) -> TitleBackfiller:
    cfg = MagicMock()
    cfg.llm_config = "config/llm.toml"
    cfg.cascade_name = "congiuntura"
    return TitleBackfiller(cfg, repo)


async def test_find_missing_uses_exists_query():
    repo = MagicMock()
    repo.find_processed_missing_field = AsyncMock(return_value=[])
    b = _make_backfiller(repo)
    await b.find_missing_title_en(limit=10)
    repo.find_processed_missing_field.assert_awaited_once_with("title_en", limit=10)


async def test_backfill_once_translates_and_updates():
    repo = MagicMock()
    repo.find_processed_missing_field = AsyncMock(return_value=[
        {"url_hash": "h1", "title": "Indice dei prezzi al consumo"},
    ])
    repo.set_processed_field = AsyncMock(return_value=True)

    fake_result = MagicMock()
    fake_result.value = _TitleTranslation(title_en="Consumer price index")

    with patch("congiuntura_live.title_backfill.generate", new=AsyncMock(return_value=fake_result)):
        b = _make_backfiller(repo)
        updated = await b.backfill_once()

    assert updated == 1
    repo.set_processed_field.assert_awaited_once_with("h1", "title_en", "Consumer price index")


async def test_backfill_once_skips_empty_titles():
    repo = MagicMock()
    repo.find_processed_missing_field = AsyncMock(return_value=[{"url_hash": "h2", "title": ""}])
    b = _make_backfiller(repo)
    assert await b.backfill_once() == 0


async def test_backfill_once_swallows_llm_errors():
    repo = MagicMock()
    repo.find_processed_missing_field = AsyncMock(return_value=[
        {"url_hash": "h3", "title": "Some title"},
    ])
    with patch("congiuntura_live.title_backfill.generate", new=AsyncMock(side_effect=RuntimeError("boom"))):
        b = _make_backfiller(repo)
        assert await b.backfill_once() == 0


async def test_backfill_all_stops_when_batch_short():
    repo = MagicMock()
    repo.find_processed_missing_field = AsyncMock(return_value=[])  # empty → stop immediately
    b = _make_backfiller(repo)
    assert await b.backfill_all() == 0


async def test_extraction_model_has_title_en():
    from congiuntura_live.settings import load_extraction_model

    model = load_extraction_model("config/extraction_model.py")
    assert "title_en" in model.model_fields
    assert "summary_en" in model.model_fields
