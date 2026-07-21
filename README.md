# 📊 Congiuntura Live

**Aggregator of RSS feeds from European official statistics agencies.**

Monitors press releases from **Eurostat**, **Istat**, **INE** (Spain), **INSEE** (France),
and **Destatis** (Germany), deduplicates them, stores them in MongoDB, and serves a
searchable web interface with real-time updates via Datastar SSE.

---

## Features

- **5 statistical agencies** monitored out of the box (11 feeds, ~300+ releases)
- **Automatic deduplication** — SHA-256 hashing of canonical URLs prevents duplicates
  across feeds and polling cycles
- **Configurable polling** — default 5 minutes, adjustable via `config/app.toml`
- **Flexible feed configuration** — add/remove feeds in `config/feeds.toml` without
  touching code; the file is re-read on every poll cycle
- **Search interface** — filter by publisher and date range, updated live via Datastar
- **Dockerized** — app + MongoDB as separate containers via Docker Compose

---

## Quick Start

### Prerequisites

- Docker and Docker Compose

### 1. Clone and configure

```bash
git clone https://github.com/paluigi-moltis/congiuntura-live.git
cd congiuntura-live
cp .env.example .env
```

### 2. Run with Docker Compose

```bash
docker compose up -d
```

The web interface is available at **http://localhost:8000**.

MongoDB runs on port **27017** with data persisted to a named volume.

### 3. Verify

```bash
curl http://localhost:8000/health
# → {"status":"ok"}
```

---

## Configuration

The application uses **three separate configuration files** for clean separation of concerns:

| File | Purpose | Committed? |
|------|---------|------------|
| `config/app.toml` | Application settings (poll interval, server, logging) | ✅ Yes |
| `config/feeds.toml` | RSS feed URLs per agency (editable) | ✅ Yes |
| `.env` | Secrets (MongoDB connection string) | ❌ Never |

### `config/app.toml`

```toml
[polling]
interval_minutes = 5     # How often to fetch all feeds

[server]
host = "0.0.0.0"
port = 8000

[logging]
level = "INFO"
```

### `config/feeds.toml`

Each agency is a TOML section. Add as many feeds per agency as you need:

```toml
[istat]
name = "Istat"
language = "it"

[[istat.feeds]]
label = "National accounts"
url = "https://www.istat.it/tema/conti-nazionali/feed"

[[istat.feeds]]
label = "Prices"
url = "https://www.istat.it/tema/prezzi/feed"
```

To add a new feed, simply add a new `[[<agency>.feeds]]` block and restart (or wait
for the next poll cycle — the file is re-read each time).

### `.env`

```env
MONGODB_URL=mongodb://mongo:27017
MONGODB_DATABASE=congiuntura
```

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                   congiuntura-live app                    │
│                                                          │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐  │
│  │  FeedReader   │──▶│  Deduplicator │──▶│  Repository  │  │
│  │ (RSS/Atom)   │   │  (URL hash)   │   │  (MongoDB)   │  │
│  └──────┬───────┘   └──────────────┘   └──────┬───────┘  │
│         ▲                                          │       │
│  ┌──────┴───────┐                           ┌─────▼─────┐ │
│  │  Scheduler    │                           │  Web UI   │ │
│  │ (APScheduler) │                           │ (Datastar)│ │
│  └──────────────┘                           │ + FastAPI │ │
│                                             └───────────┘ │
└──────────────────────────────────────────────────────────┘
         │                                           │
         ▼                                           ▼
   RSS feeds (5 agencies)                     MongoDB container
```

### Components

| Component | File | Responsibility |
|-----------|------|----------------|
| **FeedReader** | `feed_reader.py` | Async HTTP fetch (httpx) + RSS/Atom parsing (feedparser) |
| **PressReleaseRepository** | `repository.py` | Async MongoDB CRUD (motor), unique index for dedup |
| **FeedPoller** | `scheduler.py` | APScheduler periodic job orchestrating fetch → dedup → insert |
| **WebApp** | `app.py` | FastAPI + Datastar SSE for live search/filter |

### Data Model

Each press release is stored as a MongoDB document:

| Field | Type | Description |
|-------|------|-------------|
| `url_hash` | string (unique) | SHA-256 of canonical URL — dedup key |
| `url` | string | Original press release URL |
| `title` | string | Release title (cleaned of HTML entities) |
| `summary` | string | RSS description / Atom summary |
| `publisher` | string | Agency slug (e.g. `istat`) |
| `publisher_full` | string | Display name (e.g. `Istat`) |
| `feed_label` | string | Which feed within the agency |
| `language` | string | ISO code (`en`, `it`, `es`, `de`) |
| `published` | datetime | Publication date (from feed or title) |
| `fetched_at` | datetime | When the aggregator ingested it |
| `tags` | array | Topic tags from the feed (if available) |

**Indexes**: `url_hash` (unique), `publisher`, `published` (desc), `(publisher, published)`.

### Deduplication Strategy

1. Each entry's canonical URL is SHA-256 hashed → `url_hash`
2. MongoDB unique index on `url_hash` enforces dedup at insert time
3. If `url_hash` already exists → skip (already seen). If new → insert.
4. Handles cross-feed duplicates (same release in multiple topic feeds).

---

## RSS Feed Inventory

All feeds verified live as of 2025-07-21.

| Agency | Country | Language | Format | Feeds |
|--------|---------|----------|--------|-------|
| Eurostat | EU | EN | Atom | News releases |
| Istat | Italy | IT | RSS 2.0 | 6 topics (national accounts, prices, industry, services, foreign trade, labor) |
| INE | Spain | ES | RSS 2.0 | Press releases |
| INSEE | France | EN | RSS 2.0 | Economic outlook, Publications |
| Destatis | Germany | DE | RSS 2.0 | Press releases |

### Language Policy

English where available (Eurostat, INSEE English flux); native language otherwise
(Istat IT, INE ES, Destatis DE).

---

## Development

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) for dependency management
- MongoDB (local or Docker: `docker compose up mongo -d`)

### Setup

```bash
uv sync --all-extras
cp .env.example .env
# Edit .env to point to your MongoDB instance
```

### Run locally

```bash
uv run uvicorn congiuntura_live.app:app --reload
```

### Run tests

```bash
uv run pytest
```

### Lint and format

```bash
uv run ruff check src/ tests/
uv run black --check src/ tests/
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.12+ |
| Web framework | FastAPI |
| Frontend | Datastar (SSE) + Pico CSS |
| RSS parsing | feedparser |
| HTTP client | httpx (async) |
| Database | MongoDB (motor async driver) |
| Scheduler | APScheduler |
| Models | Pydantic v2 |
| Dependency management | uv |
| Containerization | Docker + Docker Compose |

---

## Project Structure

```
congiuntura-live/
├── config/
│   ├── app.toml                 # Application settings
│   └── feeds.toml               # RSS feed URLs (user-editable)
├── src/congiuntura_live/
│   ├── __init__.py
│   ├── settings.py              # Config loading (TOML + .env)
│   ├── models.py                # PressRelease Pydantic model
│   ├── feed_reader.py           # Async RSS/Atom reader
│   ├── repository.py            # Async MongoDB repository
│   ├── scheduler.py             # Periodic polling (APScheduler)
│   └── app.py                   # FastAPI app + Datastar routes
├── templates/
│   ├── base.html                # Layout: Pico CSS + Datastar CDN
│   └── index.html               # Search mask + results
├── tests/
│   ├── conftest.py              # Test fixtures (RSS/Atom samples)
│   ├── test_feed_reader.py      # Feed parsing tests
│   ├── test_dedup.py            # Deduplication tests
│   └── test_config.py           # Config loading tests
├── Dockerfile                   # Multi-stage build
├── docker-compose.yml           # App + MongoDB
├── pyproject.toml
├── .env.example
├── PLAN.md
└── README.md
```

---

## Roadmap

- [x] RSS/Atom feed aggregation (5 agencies)
- [x] Deduplication via URL hashing
- [x] MongoDB storage with indexes
- [x] Periodic polling (configurable interval)
- [x] Web search interface (publisher + date range)
- [x] Dockerization
- [ ] LLM processing with Llama + outlines-cascade (Phase 2)
- [ ] Full-text search of press release bodies
- [ ] Notifications (email/Slack)

---

## License

MIT © Luigi Palumbo

---

## Change Log

- **0.1.0** (2025-07-21): Initial release. RSS aggregation from 5 agencies, dedup,
  MongoDB storage, Datastar search UI, Docker Compose deployment.
