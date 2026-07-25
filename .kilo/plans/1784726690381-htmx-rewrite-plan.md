# Plan: Replace Datastar with htmx

## Goal

Replace the Datastar SDK frontend with htmx. The Datastar SDK has persistent,
unreproducible-in-headless bugs (filters don't fire `change` listeners, signal
binding conflicts). htmx is mandated by the project's `global_rules.md` and is a
mature, well-documented alternative.

## Scope

- **In scope:** All 4 Jinja2 templates (`base`, `index`, `raw` + new partials),
  the FastAPI route handlers in `app.py`, vendoring htmx locally, Dockerfile
  static file copying.
- **Out of scope:** The backend repository, processor, scheduler, feed reader,
  extraction model, and MongoDB logic remain unchanged. No new Python deps
  beyond removing `datastar-py`.

## Architecture

### Request/response model

| Route | Method | Returns | htmx target |
|-------|--------|---------|------------|
| `/` | GET | Full `index.html` page | — |
| `/raw` | GET | Full `raw.html` page | — |
| `/search` | GET | HTML fragment: processed cards | `#results` |
| `/search-raw` | GET | HTML fragment: raw table rows | `#results` |
| `/reprocess` | POST | Sets `processing=True`, returns 204 (no content) | swaps button to spinner state |
| `/processing-status` | GET | HTML fragment: spinner + counts or just counts | `#stats` |
| `/health` | GET | JSON `{"status":"ok"}` | — |

### Filter mechanism

- Each `<select multiple>` has `name="topic"` etc. and
  `hx-get="/search" hx-trigger="change" hx-target="#results"`.
- `hx-include="closest form"` bundles ALL filter values on every change.
- All filters wrapped in a single `<form>` so `hx-include` collects everything.
- FastAPI receives `topic: list[str] = Query(default=[])` — repeated query
  params become a list automatically.
- A small hint text under each multi-select: "Hold Ctrl/Cmd to select multiple".

### Processing flag + spinner

- A module-level `_processing = False` flag in `app.py`.
- `ProcessingPoller.process_once()` and `process_all_pending()` set it `True`
  at start, `False` at end (wrap in try/finally).
- `/reprocess` (POST): sets flag True, kicks off
  `asyncio.create_task(_proc_poller.process_all_pending())`, returns 204.
- `#stats` div has `hx-get="/processing-status" hx-trigger="every 2s"` — polls
  the flag. When True, response includes spinner + current counts. When False,
  response includes counts only and htmx `hx-swap-oob` stops showing spinner.
- The reprocess button: `hx-post="/reprocess" hx-target="#stats"` so clicking
  it swaps `#stats` into spinner state immediately.

### Static file serving

- Vendor `htmx.min.js` (v2.x) into `templates/static/htmx.min.js`.
- Add `app.mount("/static", StaticFiles(directory=TEMPLATES_DIR / "static"))`
  in `app.py`.
- `base.html` loads `<script src="/static/htmx.min.js"></script>`.

## Files to modify

### 1. `pyproject.toml`
- Remove `"datastar-py>=0.4.0"` from dependencies.
- Run `uv lock` to regenerate `uv.lock`.

### 2. `src/congiuntura_live/app.py`
- Remove `datastar_py` imports.
- Add `from starlette.staticfiles import StaticFiles`.
- Add `app.mount("/static", StaticFiles(...))` after `app` creation.
- Add module-level `_processing: bool = False`.
- Rewrite `/search`: accept `topic`, `country`, `sentiment`, `publisher` as
  `list[str]` query params, `date_from`/`date_to` as `str`. Return Jinja2
  rendered fragment (not SSE).
- Rewrite `/search-raw`: same pattern.
- Add `POST /reprocess`: set flag, create background task, return 204.
- Add `GET /processing-status`: render `#stats` partial with flag + counts.
- Remove `_render_processed_fragment` / `_render_raw_fragment` string builders
  (move HTML to Jinja2 partials).
- Update `ProcessingPoller` to set/clear `_processing` flag (import from app or
  pass a callback). Preferred: add a `set_processing_flag` callable parameter
  to `ProcessingPoller.__init__`.

### 3. `src/congiuntura_live/scheduler.py`
- `ProcessingPoller.__init__` gets optional `on_processing_change: Callable[[bool], None]`.
- `process_once()` and `process_all_pending()` call it True at start, False in
  finally block.

### 4. Templates (full rewrite)

#### `templates/base.html`
- Replace Datastar SDK `<script>` with `<script src="/static/htmx.min.js">`.
- Keep Pico CSS, all existing `<style>` blocks.
- Add `hx-boost` on `<body>` for smooth page transitions.

#### `templates/index.html`
- Wrap filters in `<form id="filter-form" hx-include="this">`.
- Each `<select multiple name="X">`:
  `hx-get="/search" hx-trigger="change" hx-target="#results" hx-swap="innerHTML"`.
- Date inputs same pattern.
- Reprocess button: `hx-post="/reprocess" hx-target="#stats"`.
- `#stats` div: `hx-get="/processing-status" hx-trigger="every 2s"`.
- `#results` div: initial state "Loading…", loaded via
  `hx-get="/search" hx-trigger="load"`.

#### `templates/raw.html`
- Same pattern, targets `/search-raw`.
- Single-select publisher (no multi-select needed on raw page).

#### `templates/_processed_cards.html` (NEW)
- Jinja2 partial: iterates `results`, renders `<article class="card">` blocks.
- Receives `results` list from `/search` handler.

#### `templates/_raw_rows.html` (NEW)
- Jinja2 partial: iterates `results`, renders `<tr>` rows.

#### `templates/_stats.html` (NEW)
- Jinja2 partial: renders counts + optional spinner.
- Receives `processing: bool`, `processed_count: int`, `raw_count: int`.

### 5. `templates/static/htmx.min.js` (NEW)
- Download htmx v2.0.x minified from unpkg.

### 6. `Dockerfile`
- No change needed — `templates/` is already copied (line 13, 30), which now
  includes `static/` subdirectory.

## Validation plan

1. `docker compose up -d --build` — app starts without import errors.
2. `curl localhost:8000/` — full HTML page loads, htmx script tag present.
3. `curl localhost:8000/search?topic=GDP` — returns HTML fragment with cards.
4. `curl localhost:8000/search?topic=GDP&topic=Construction` — multi-value works.
5. Headless Chromium: load `/`, verify 200 articles in `#results`.
6. Headless Chromium: change topic select, verify `#results` updates via htmx.
7. Headless Chromium: click reprocess, verify spinner appears in `#stats`.
8. `curl localhost:8000/static/htmx.min.js` — static file served correctly.

## Risks

- **htmx `change` trigger on `<select multiple>`**: htmx fires on every option
  toggle (each Ctrl+click). May cause rapid requests. Mitigation: add
  `hx-trigger="change delay:200ms"` to debounce.
- **Background task without awaiting**: `asyncio.create_task` in a FastAPI
  sync-ish context may need careful handling. Mitigation: ensure the route is
  `async def` and the task is fire-and-forget (the flag protects against
  concurrent reprocessing).
- **Processing flag race**: if background poller and manual button overlap.
  Mitigation: flag is a simple boolean; both set True/False, final state
  converges to False when all work done. Worst case: spinner flickers.
