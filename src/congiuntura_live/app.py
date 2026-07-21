"""FastAPI application with Datastar-powered search interface."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from datastar_py import ServerSentEventGenerator as SSE
from datastar_py.fastapi import datastar_response, read_signals
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .feed_reader import FeedReader
from .repository import PressReleaseRepository
from .scheduler import FeedPoller
from .settings import Settings, load_app_config, load_feeds_config

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"

# Global objects populated at startup.
_repo: PressReleaseRepository | None = None
_reader: FeedReader | None = None
_poller: FeedPoller | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle."""
    global _repo, _reader, _poller

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

    logger.info("Application started — polling every %d min", app_cfg.polling.interval_minutes)
    yield

    if _poller:
        await _poller.stop()
    if _repo:
        await _repo.close()
    logger.info("Application stopped")


app = FastAPI(title="Congiuntura Live", version="0.1.0", lifespan=lifespan)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _get_repo() -> PressReleaseRepository:
    if _repo is None:
        raise RuntimeError("Repository not initialized")
    return _repo


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Render the main search page."""
    feeds_cfg = load_feeds_config()
    publishers = [(slug, cfg.name) for slug, cfg in feeds_cfg.items()]
    repo = _get_repo()
    total = await repo.count_total()
    return templates.TemplateResponse(
        request,
        "index.html",
        {"request": request, "publishers": publishers, "total": total},
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


def _parse_date_signal(raw: str | None) -> datetime | None:
    """Parse a YYYY-MM-DD date string from the frontend to an aware datetime."""
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return None


@app.get("/search")
@datastar_response
async def search(request: Request):
    """Datastar SSE endpoint: filters and streams results fragment.

    Reads Datastar signals: publisher, date_from, date_to.
    """
    sig_data = await read_signals(request) or {}
    publisher = sig_data.get("publisher", "all")
    date_from = _parse_date_signal(sig_data.get("date_from"))
    date_to = _parse_date_signal(sig_data.get("date_to"))

    repo = _get_repo()
    results = await repo.search(
        publisher=publisher, date_from=date_from, date_to=date_to, limit=200
    )

    html = _render_results_fragment(results)
    yield SSE.patch_elements(html)


def _render_results_fragment(results: list[dict[str, Any]]) -> str:
    """Render the results table as an HTML fragment for Datastar merge."""
    rows: list[str] = []
    for r in results:
        pub_date = r.get("published")
        date_str = ""
        if pub_date:
            try:
                dt = datetime.fromisoformat(pub_date) if isinstance(pub_date, str) else pub_date
                date_str = dt.strftime("%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                date_str = str(pub_date)[:16]

        title = r.get("title", "")
        url = r.get("url", "#")
        publisher = r.get("publisher_full", r.get("publisher", ""))
        feed_label = r.get("feed_label", "")
        summary = _escape_html((r.get("summary") or "")[:200])

        rows.append(
            "<tr>"
            f'<td><span class="badge">{_escape_html(publisher)}</span></td>'
            f"<td>{date_str}</td>"
            f'<td><a href="{url}" target="_blank">{_escape_html(title)}</a>'
            f'<div class="feed-label">{_escape_html(feed_label)}</div>'
            f'<div class="summary">{summary}</div></td>'
            "</tr>"
        )

    if not rows:
        body = '<tr><td colspan="3" class="empty">No results found.</td></tr>'
    else:
        body = "\n".join(rows)

    return f'<div id="results"><table>{body}</table></div>'


def _escape_html(text: str) -> str:
    import html as html_mod

    return html_mod.escape(text)
