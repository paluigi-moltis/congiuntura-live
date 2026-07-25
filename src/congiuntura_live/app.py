"""FastAPI application with htmx-powered search interface."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, get_args, get_origin

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
from starlette.responses import Response

from .feed_reader import FeedReader
from .processor import ReleaseProcessor
from .repository import PressReleaseRepository
from .scheduler import FeedPoller, ProcessingPoller
from .settings import Settings, load_app_config, load_extraction_model, load_feeds_config

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"

# Global objects populated at startup.
_repo: PressReleaseRepository | None = None
_reader: FeedReader | None = None
_poller: FeedPoller | None = None
_processor: ReleaseProcessor | None = None
_proc_poller: ProcessingPoller | None = None
_extraction_model_class = None

# Module-level processing reference counter, incremented by ProcessingPoller
# and the manual reprocess button.  Polled by the htmx /processing-status endpoint.
_processing_refs: int = 0


def _is_processing() -> bool:
    return _processing_refs > 0


def _processing_inc() -> None:
    global _processing_refs
    _processing_refs += 1


def _processing_dec() -> None:
    global _processing_refs
    _processing_refs = max(0, _processing_refs - 1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle."""
    global _repo, _reader, _poller, _processor, _proc_poller, _extraction_model_class

    settings = Settings()
    app_cfg = load_app_config()

    logging.basicConfig(
        level=getattr(logging, app_cfg.logging.level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    _repo = PressReleaseRepository(settings.mongodb_url, settings.mongodb_database)
    await _repo.ensure_indexes()

    _reader = FeedReader()
    _poller = FeedPoller(_reader, _repo)
    _poller.start(interval_minutes=app_cfg.polling.interval_minutes)

    # Load extraction model for UI introspection
    _extraction_model_class = load_extraction_model(app_cfg.processing.model_path)

    # Start processing pipeline if enabled
    if app_cfg.processing.enabled:
        try:
            _processor = ReleaseProcessor(app_cfg.processing)
            _proc_poller = ProcessingPoller(
                _processor,
                _repo,
                interval_minutes=app_cfg.processing.interval_minutes,
                on_processing_change=lambda active: _processing_inc() if active else _processing_dec(),
            )
            _proc_poller.start()
            logger.info("LLM processing enabled — cascade '%s'", app_cfg.processing.cascade_name)
        except Exception:
            logger.exception("Failed to start LLM processing — continuing in raw-only mode")
            _processor = None
            _proc_poller = None

    logger.info("Application started — polling every %d min", app_cfg.polling.interval_minutes)
    yield

    if _proc_poller:
        await _proc_poller.stop()
    if _poller:
        await _poller.stop()
    if _repo:
        await _repo.close()
    logger.info("Application stopped")


app = FastAPI(title="Congiuntura Live", version="0.3.0", lifespan=lifespan)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Serve vendored static assets (htmx.min.js etc.)
app.mount("/static", StaticFiles(directory=str(TEMPLATES_DIR / "static")), name="static")


def _get_repo() -> PressReleaseRepository:
    if _repo is None:
        raise RuntimeError("Repository not initialized")
    return _repo


# ── Template filters ────────────────────────────────────────


def _format_date(pub_date: Any) -> str:
    if not pub_date:
        return ""
    try:
        dt = datetime.fromisoformat(pub_date) if isinstance(pub_date, str) else pub_date
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return str(pub_date)[:16]


templates.env.filters["format_date"] = _format_date


# ── Model introspection for auto-generated filters ──────────


def _build_filter_definitions() -> list[dict[str, Any]]:
    """Introspect the LLMExtraction model to build UI filter definitions.

    Only Literal types become dropdown filters.  ``str`` fields
    (summary_en, key_figures) are excluded — they are display-only.
    """
    if _extraction_model_class is None:
        return []
    filters: list[dict[str, Any]] = []
    for name, field_info in _extraction_model_class.model_fields.items():
        annotation = field_info.annotation
        origin = get_origin(annotation)
        if origin is not None and hasattr(origin, "__name__") and origin.__name__ == "Literal":
            choices = list(get_args(annotation))
            filters.append({"name": name, "type": "select", "choices": choices})
    return filters


# ── Full page routes ────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Render the main page with processed releases and auto-generated filters."""
    feeds_cfg = load_feeds_config()
    publishers = [(slug, cfg.name) for slug, cfg in feeds_cfg.items()]
    repo = _get_repo()
    processed_count = await repo.count_total_processed()
    raw_count = await repo.count_total_raw()
    filters = _build_filter_definitions()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "publishers": publishers,
            "processed_count": processed_count,
            "raw_count": raw_count,
            "filters": filters,
            "processing_enabled": _processor is not None,
        },
    )


@app.get("/raw", response_class=HTMLResponse)
async def raw(request: Request):
    """Render the secondary page with raw (unprocessed) feeds."""
    feeds_cfg = load_feeds_config()
    publishers = [(slug, cfg.name) for slug, cfg in feeds_cfg.items()]
    repo = _get_repo()
    total = await repo.count_total_raw()
    return templates.TemplateResponse(
        request,
        "raw.html",
        {
            "request": request,
            "publishers": publishers,
            "total": total,
        },
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


# ── Date parsing helper ─────────────────────────────────────


def _parse_date(raw: str | None) -> str | None:
    """Parse a ``YYYY-MM-DD`` date picker value into an ISO 8601 string.

    The ``published`` field is stored as an ISO 8601 string in MongoDB,
    so date-range filters must also be strings for correct comparison.
    """
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=UTC).isoformat()
    except ValueError:
        return None


# ── htmx fragment routes ────────────────────────────────────


@app.get("/search", response_class=HTMLResponse)
async def search(
    request: Request,
    publisher: list[str] = Query(default=[]),
    topic: list[str] = Query(default=[]),
    country: list[str] = Query(default=[]),
    sentiment: list[str] = Query(default=[]),
    date_from: str = Query(default=""),
    date_to: str = Query(default=""),
):
    """htmx endpoint: filter processed releases, return card fragment."""
    repo = _get_repo()
    filters: dict[str, Any] = {}
    for key, vals in (("publisher", publisher), ("topic", topic), ("country", country), ("sentiment", sentiment)):
        if vals:
            filters[key] = vals
    filters["date_from"] = _parse_date(date_from)
    filters["date_to"] = _parse_date(date_to)

    results = await repo.search_processed(filters=filters, limit=200)
    return templates.TemplateResponse(
        request,
        "_processed_cards.html",
        {"request": request, "results": results},
    )


@app.get("/search-raw", response_class=HTMLResponse)
async def search_raw(
    request: Request,
    publisher: str = Query(default="all"),
    date_from: str = Query(default=""),
    date_to: str = Query(default=""),
):
    """htmx endpoint: filter raw releases, return table fragment."""
    repo = _get_repo()
    results = await repo.search_raw(
        publisher=publisher,
        date_from=_parse_date(date_from),
        date_to=_parse_date(date_to),
        limit=200,
    )
    return templates.TemplateResponse(
        request,
        "_raw_rows.html",
        {"request": request, "results": results},
    )


# ── Reprocess + processing status ───────────────────────────


@app.post("/reprocess")
async def reprocess(request: Request):
    """Kick off manual reprocessing as a background task.

    Sets the processing flag immediately (so the spinner shows), then
    runs the full pending queue.  The flag is cleared when done.
    """
    if _proc_poller is None:
        return Response(
            content='<span id="reprocess-status" class="flash">Processing is not enabled.</span>',
            media_type="text/html",
            status_code=200,
        )

    if _is_processing():
        return Response(
            content='<span id="reprocess-status" class="flash">Already processing — please wait.</span>',
            media_type="text/html",
            status_code=200,
        )

    async def _run():
        _processing_inc()
        try:
            await _proc_poller.process_all_pending()
        except Exception:
            logger.exception("Background reprocess failed")
        finally:
            _processing_dec()

    background = BackgroundTask(_run)

    return templates.TemplateResponse(
        request,
        "_stats.html",
        {
            "request": request,
            "processing": True,
            "processed_count": await _get_repo().count_total_processed(),
            "raw_count": await _get_repo().count_total_raw(),
            "reprocess_msg": "Processing started…",
        },
        background=background,
    )


@app.get("/processing-status", response_class=HTMLResponse)
async def processing_status(request: Request):
    """Return the stats bar fragment (polled by htmx every 2s).

    Includes a spinner when processing is active.
    """
    repo = _get_repo()
    processed_count, raw_count = await asyncio.gather(
        repo.count_total_processed(), repo.count_total_raw()
    )
    return templates.TemplateResponse(
        request,
        "_stats.html",
        {
            "request": request,
            "processing": _is_processing(),
            "processed_count": processed_count,
            "raw_count": raw_count,
        },
    )
