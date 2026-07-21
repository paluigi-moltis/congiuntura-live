"""FastAPI application with Datastar-powered search interface."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, get_args, get_origin

from datastar_py import ServerSentEventGenerator as SSE
from datastar_py.fastapi import datastar_response, read_signals
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

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
                _processor, _repo, interval_minutes=app_cfg.processing.interval_minutes
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


app = FastAPI(title="Congiuntura Live", version="0.2.0", lifespan=lifespan)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _get_repo() -> PressReleaseRepository:
    if _repo is None:
        raise RuntimeError("Repository not initialized")
    return _repo


# ── Model introspection for auto-generated filters ──────────


def _build_filter_definitions() -> list[dict[str, Any]]:
    """Introspect the LLMExtraction model to build UI filter definitions.

    Literal types → dropdown, str → text search, etc.
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
        elif annotation is str:
            filters.append({"name": name, "type": "text"})
    return filters


# ── Main page: processed releases ───────────────────────────


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Render the main page with processed releases and auto-generated filters."""
    feeds_cfg = load_feeds_config()
    publishers = [(slug, cfg.name) for slug, cfg in feeds_cfg.items()]
    repo = _get_repo()
    total = await repo.count_total_processed()
    total_raw = await repo.count_total_raw()
    filters = _build_filter_definitions()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "publishers": publishers,
            "total": total,
            "total_raw": total_raw,
            "filters": filters,
            "processing_enabled": _processor is not None,
        },
    )


# ── Raw feeds page (secondary) ──────────────────────────────


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


def _parse_date_signal(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return None


# ── Datastar SSE: processed search ──────────────────────────


@app.get("/search")
@datastar_response
async def search(request: Request):
    """Datastar SSE endpoint: filters and streams processed results."""
    sig_data = await read_signals(request) or {}
    repo = _get_repo()

    filters: dict[str, Any] = {}
    for key in ("publisher", "topic", "country", "sentiment"):
        val = sig_data.get(key, "all")
        if val and val != "all":
            filters[key] = val
    for key in ("summary_en", "key_figures"):
        val = sig_data.get(key, "")
        if val:
            filters[key] = val
    filters["date_from"] = _parse_date_signal(sig_data.get("date_from"))
    filters["date_to"] = _parse_date_signal(sig_data.get("date_to"))

    results = await repo.search_processed(filters=filters, limit=200)
    html = _render_processed_fragment(results)
    yield SSE.patch_elements(html)


# ── Datastar SSE: raw search ────────────────────────────────


@app.get("/search-raw")
@datastar_response
async def search_raw(request: Request):
    """Datastar SSE endpoint: filters and streams raw results."""
    sig_data = await read_signals(request) or {}
    repo = _get_repo()

    publisher = sig_data.get("publisher", "all")
    date_from = _parse_date_signal(sig_data.get("date_from"))
    date_to = _parse_date_signal(sig_data.get("date_to"))

    results = await repo.search_raw(
        publisher=publisher, date_from=date_from, date_to=date_to, limit=200
    )
    html = _render_raw_fragment(results)
    yield SSE.patch_elements(html)


# ── Reprocess endpoint ──────────────────────────────────────


@app.get("/reprocess")
@datastar_response
async def reprocess(request: Request):
    """Trigger incremental batch processing of all unprocessed items.

    Processes only items present in raw but NOT in processed collection.
    """
    if _proc_poller is None:
        yield SSE.patch_elements(
            '<div id="results"><p class="empty">Processing is not enabled.</p></div>'
        )
        return

    count = await _proc_poller.process_all_pending()
    html = f'<div id="reprocess-status" class="flash">' f"Processed {count} new releases." "</div>"
    yield SSE.patch_elements(html)


# ── HTML fragment renderers ─────────────────────────────────


def _escape(text: str) -> str:
    import html as html_mod

    return html_mod.escape(str(text))


def _format_date(pub_date) -> str:
    if not pub_date:
        return ""
    try:
        dt = datetime.fromisoformat(pub_date) if isinstance(pub_date, str) else pub_date
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return str(pub_date)[:16]


def _render_processed_fragment(results: list[dict[str, Any]]) -> str:
    """Render processed release cards as an HTML fragment."""
    cards: list[str] = []
    for r in results:
        publisher = r.get("publisher_full", r.get("publisher", ""))
        publisher_slug = r.get("publisher", "")
        date_str = _format_date(r.get("published"))
        title = _escape(r.get("title", ""))
        url = _escape(r.get("url", "#"))
        topic = _escape(r.get("topic", ""))
        country = _escape(r.get("country", ""))
        sentiment = _escape(r.get("sentiment", ""))
        summary_en = _escape(r.get("summary_en", ""))
        key_figures = _escape(r.get("key_figures", ""))
        model = _escape(r.get("processing_model", ""))
        feed_label = _escape(r.get("feed_label", ""))

        sentiment_class = {
            "positive": "sentiment-positive",
            "negative": "sentiment-negative",
            "neutral": "sentiment-neutral",
        }.get(sentiment, "sentiment-neutral")

        cards.append(
            f'<article class="card">'
            f'<div class="card-header">'
            f'<span class="badge {publisher_slug}">{_escape(publisher)}</span>'
            f'<span class="topic">{topic}</span>'
            f'<span class="country">{country}</span>'
            f'<span class="sentiment {sentiment_class}">{sentiment}</span>'
            f"</div>"
            f'<h3><a href="{url}" target="_blank">{title}</a></h3>'
            f'<p class="summary-en">{summary_en}</p>'
            f'<div class="key-figures">{key_figures}</div>'
            f'<div class="card-footer">'
            f'<span class="date">{date_str}</span>'
            f'<span class="feed-label">{feed_label}</span>'
            f'<span class="model">🤖 {model}</span>'
            f"</div>"
            f"</article>"
        )

    if not cards:
        body = '<p class="empty">No processed releases found.</p>'
    else:
        body = "\n".join(cards)

    return f'<div id="results">{body}</div>'


def _render_raw_fragment(results: list[dict[str, Any]]) -> str:
    """Render raw release rows as an HTML fragment."""
    rows: list[str] = []
    for r in results:
        date_str = _format_date(r.get("published"))
        title = _escape(r.get("title", ""))
        url = _escape(r.get("url", "#"))
        publisher = _escape(r.get("publisher_full", r.get("publisher", "")))
        publisher_slug = r.get("publisher", "")
        feed_label = _escape(r.get("feed_label", ""))
        summary = _escape((r.get("summary") or "")[:200])

        rows.append(
            "<tr>"
            f'<td><span class="badge {publisher_slug}">{publisher}</span></td>'
            f"<td>{date_str}</td>"
            f'<td><a href="{url}" target="_blank">{title}</a>'
            f'<div class="feed-label">{feed_label}</div>'
            f'<div class="summary">{summary}</div></td>'
            "</tr>"
        )

    if not rows:
        body = '<tr><td colspan="3" class="empty">No results found.</td></tr>'
    else:
        body = "\n".join(rows)

    return f'<div id="results"><table>{body}</table></div>'
