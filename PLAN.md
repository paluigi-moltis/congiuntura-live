# PLAN.md — congiuntura-live

## Overview

A real-time aggregator of RSS/Atom feeds from 5 European official statistics agencies.
Press releases are fetched periodically, deduplicated, and stored in MongoDB.
A web interface (FastAPI + Datastar) lets the user filter by publisher and date range.

The name *Congiuntura Live* reflects the focus on **economic conjuncture** (consumer prices,
producer prices, GDP, industrial production, international trade, etc.).

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                   congiuntura-live app                    │
│                                                          │
│  ┌──────────────┐   ┌─────────────┐   ┌───────────────┐  │
│  │  FeedReader   │──▶│  Deduplicator│──▶│  Repository   │  │
│  │ (RSS/Atom)   │   │  (URL hash)  │   │  (MongoDB)    │  │
│  └──────────────┘   └─────────────┘   └───────┬───────┘  │
│          ▲                                      │         │
│          │                                ┌─────▼───────┐  │
│   ┌──────┴───────┐                        │  Web UI     │  │
│   │  Scheduler    │                        │ (Datastar)  │  │
│   │ (APScheduler) │                        │ + FastAPI   │  │
│   └──────────────┘                        └─────────────┘  │
└──────────────────────────────────────────────────────────┘
         │                                          │
         ▼                                          ▼
   RSS feeds (5 agencies)                    MongoDB container
```

### Components

| Component | Responsibility |
|-----------|---------------|
| `FeedReader` | Fetches and parses RSS/Atom feeds, normalizes entries to `PressRelease` |
| `Deduplicator` | Uses URL-based SHA-256 hash to detect duplicates across feeds and runs |
| `Repository` | Async MongoDB CRUD (motor), indexes on `publisher`, `published`, `url_hash` |
| `Scheduler` | APScheduler periodic job that polls all configured feeds |
| `WebApp` | FastAPI app serving Jinja2 templates + Datastar SSE for search/filter |

### Tech Stack

- **Python 3.12+** with `uv` for dependency management
- **FastAPI** — async web framework
- **Datastar** (`datastar-py`) — hypermedia frontend (SSE)
- **Motor** — async MongoDB driver
- **feedparser** — RSS/Atom parsing
- **APScheduler** — periodic feed polling
- **Pydantic v2** — data models and settings
- **Jinja2** — server-side HTML templates
- **Pico CSS** — classless CSS for Datastar pages (CDN)
- **Docker / Docker Compose** — containerization

---

## RSS Feed Inventory (verified 2025-07-21)

All feed URLs are stored in `config/feeds.toml` and can be edited manually.

| Agency | Language | Format | Confirmed URL(s) |
|--------|----------|--------|------------------|
| **Eurostat** | EN | Atom | `https://ec.europa.eu/eurostat/en/search?...resource_id=atom&...collection=CAT_PREREL` |
| **Istat** | IT | RSS 2.0 | Per-topic: `https://www.istat.it/tema/{topic}/feed` |
| **INE (Spain)** | ES | RSS 2.0 | `https://ine.es/dyngs/Prensa/es/rssNovedades.xml` |
| **INSEE (France)** | EN | RSS 2.0 | `https://www.insee.fr/en/flux/30` (Economic outlook), `/flux/31` (Publications) |
| **Destatis (Germany)** | DE | RSS 2.0 | `https://www.destatis.de/Aktuelles.xml` |

> **Language policy**: English where available (Eurostat, INSEE English flux), native
> language otherwise (Istat IT, INE ES, Destatis DE). INSEE offers both `/fr/flux/N`
> and `/en/flux/N` — we default to EN for the economic feeds.

---

## Data Model

### `PressRelease` (MongoDB document)

```json
{
  "_id": ObjectId,
  "url_hash": "sha256hex",       // dedup key — hash of canonical URL
  "url": "https://...",           // original press release URL
  "title": "...",
  "summary": "...",               // RSS <description> or Atom <summary>
  "publisher": "istat",           // agency slug
  "publisher_full": "Istat",      // display name
  "feed_label": "Conti nazionali",// which feed/topic within the agency
  "language": "it",
  "published": ISODate,           // publication date from feed
  "fetched_at": ISODate,          // when we ingested it
  "tags": ["prices", "inflation"] // optional topic tags from feed
}
```

**Indexes**: `url_hash` (unique), `publisher`, `published` (descending), `(publisher, published)`.

### Deduplication Strategy

1. Each entry's canonical URL is SHA-256 hashed → `url_hash`
2. Before insert, check if `url_hash` already exists in MongoDB
3. If exists → skip (already seen). If new → insert.
4. This handles cross-feed duplicates (same press release appearing in multiple feeds).

---

## Project Structure

```
congiuntura-live/
├── config/
│   └── feeds.toml              # Editable RSS feed configuration
├── src/
│   └── congiuntura_live/
│       ├── __init__.py
│       ├── settings.py          # Pydantic settings from .env
│       ├── models.py            # PressRelease Pydantic model
│       ├── feed_reader.py       # FeedReader class
│       ├── repository.py        # Async MongoDB repository
│       ├── scheduler.py         # APScheduler integration
│       └── app.py               # FastAPI app + Datastar routes
├── templates/
│   ├── base.html                # Layout: Pico CSS + Datastar CDN
│   ├── index.html               # Search mask + results
│   └── _results.html            # Partial: results table fragment
├── tests/
│   ├── test_feed_reader.py      # Parsing tests with fixture XML
│   ├── test_dedup.py            # Deduplication logic
│   └── test_repository.py       # Repository (mocked Mongo)
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── pyproject.toml
├── PLAN.md
└── README.md
```

---

## Implementation Phases

### Phase 1: Project scaffold + config
- `pyproject.toml` with `uv`, Ruff, Black
- `config/feeds.toml` — per-agency editable feed config
- `settings.py` — Pydantic `BaseSettings` loading from `.env`
- `.env.example`

### Phase 2: Core ingestion
- `models.py` — `PressRelease` Pydantic model
- `feed_reader.py` — `FeedReader` class (async HTTP fetch + feedparser)
- `repository.py` — async MongoDB repository (motor)
- Deduplication via `url_hash`

### Phase 3: Scheduler
- `scheduler.py` — APScheduler `AsyncIOScheduler`
- Configurable poll interval (default: 30 min)

### Phase 4: Web UI
- `app.py` — FastAPI app with Datastar SSE endpoints
- Search mask: publisher dropdown + date range (from/to)
- Results rendered as HTML fragments via Datastar
- Pico CSS for styling

### Phase 5: Dockerization
- `Dockerfile` (multi-stage, slim image)
- `docker-compose.yml` (app + MongoDB as separate containers)

### Phase 6: Tests + docs
- Pytest tests for feed parsing, dedup, repository
- `README.md` with setup, usage, architecture docs
- Push to GitHub

---

## Configuration

### `config/feeds.toml` (user-editable)

```toml
[eurostat]
name = "Eurostat"
language = "en"

[[eurostat.feeds]]
label = "News releases"
url = "https://ec.europa.eu/eurostat/en/search?...atom...CAT_PREREL"

[istat]
name = "Istat"
language = "it"

[[istat.feeds]]
label = "National accounts"
url = "https://www.istat.it/tema/conti-nazionali/feed"

# ... more feeds per agency
```

### `.env` (secrets)

```env
MONGODB_URL=mongodb://mongo:27017
MONGODB_DATABASE=congiuntura
POLL_INTERVAL_MINUTES=30
LOG_LEVEL=INFO
```

---

## Out of Scope (Phase 2 — future)

- LLM processing with Llama + outlines-cascade
- Full-text search of press release bodies
- Email/Slack notifications
- User authentication
