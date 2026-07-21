# 📊 Congiuntura Live

**Aggregator and LLM processor of RSS feeds from European official statistics agencies.**

Monitors press releases from **Eurostat**, **Istat**, **INE** (Spain), **INSEE** (France),
and **Destatis** (Germany), deduplicates them, stores them in MongoDB, processes them
with structured LLM extraction via [outlines-cascade](https://pypi.org/project/outlines-cascade/),
and serves a searchable web interface with real-time updates via Datastar SSE.

---

## Features

### Phase 1 — Feed aggregation (complete)

- **5 statistical agencies** monitored (11 feeds, ~300+ releases)
- **Automatic deduplication** via SHA-256 URL hashing
- **Configurable polling** — default 5 minutes
- **Flexible feed configuration** — edit `config/feeds.toml` without touching code
- **Dockerized** — app + MongoDB as separate containers

### Phase 2 — LLM processing (complete)

- **Structured extraction** via outlines-cascade (topic, country, sentiment, EN summary, key figures)
- **Anti-hallucination design** — the LLM never sees URLs, dates, or publishers; those are
  copied verbatim from the raw feed after generation
- **Content scraping** — trafilatura extracts the full press release text for richer LLM input
- **Configurable extraction model** — edit `config/extraction_model.py` to change what the
  LLM extracts; the web UI auto-generates filter controls from the model
- **Cloud LLM backend** — OpenRouter (OpenAI-compatible) as primary, with cascade failover
- **Incremental processing** — only processes raw items not yet in the processed collection
- **Auto-generated filter UI** — dropdowns for Literal fields, text search for str fields

---

## Quick Start

### Prerequisites

- Docker and Docker Compose
- An OpenRouter API key (free tier available at [openrouter.ai](https://openrouter.ai))

### 1. Clone and configure

```bash
git clone https://github.com/paluigi-moltis/congiuntura-live.git
cd congiuntura-live
cp .env.example .env
```

### 2. Set your API key

Edit `.env` and add your OpenRouter key:

```env
OPENROUTER_API_KEY=sk-or-v1-your-key-here
```

### 3. Run with Docker Compose

```bash
docker compose up -d
```

- **Web interface:** http://localhost:8000
- **MongoDB:** port 27017 (data persisted to named volume)

### 4. Verify

```bash
curl http://localhost:8000/health
# → {"status":"ok"}
```

---

## Configuration

### File separation principle

| File | Purpose | Committed? | Secrets? |
|------|---------|------------|----------|
| `config/app.toml` | App settings (polling, processing, server) | ✅ | ❌ |
| `config/feeds.toml` | RSS feed URLs per agency | ✅ | ❌ |
| `config/extraction_model.py` | Pydantic model for LLM extraction | ✅ | ❌ |
| `config/llm.toml` | outlines-cascade providers + cascades | ✅ | ❌ |
| `.env` | Secrets (MongoDB URL, API keys) | ❌ | ✅ |

**API keys are NEVER in TOML files** — only the environment variable name
(`api_key_env`). The actual key goes in `.env`.

### `config/app.toml`

```toml
[polling]
interval_minutes = 5

[server]
host = "0.0.0.0"
port = 8000

[logging]
level = "INFO"

[processing]
enabled = true
llm_config = "config/llm.toml"
cascade_name = "conjuncture"
interval_minutes = 2
max_content_chars = 4000
model_path = "config/extraction_model.py"
```

### `config/feeds.toml`

Add feeds per agency — the file is re-read on every poll cycle:

```toml
[istat]
name = "Istat"
language = "it"

[[istat.feeds]]
label = "National accounts"
url = "https://www.istat.it/tema/conti-nazionali/feed"
```

### `config/extraction_model.py`

Define what the LLM extracts. The web UI auto-generates filters from this model:

```python
class LLMExtraction(BaseModel):
    topic: Literal["Consumer prices", "Producer prices", ...] = Field(...)
    country: Literal["Euro area", "Italy", ...] = Field(...)
    sentiment: Literal["positive", "negative", "neutral"] = Field(...)
    summary_en: str = Field(description="Concise English summary")
    key_figures: str = Field(description="Key numerical figures")
```

### `config/llm.toml`

```toml
[providers.openrouter]
type = "openai"
base_url = "https://openrouter.ai/api/v1"
api_key_env = "OPENROUTER_API_KEY"   # ← variable name only; key goes in .env

[cascades.conjuncture]
entries = [
    { provider = "openrouter", model = "meta-llama/llama-3.3-70b-instruct" },
    { provider = "ollama", model = "llama3.1" },
]
```

### `.env`

```env
MONGODB_URL=mongodb://mongo:27017
MONGODB_DATABASE=congiuntura
OPENROUTER_API_KEY=sk-or-v1-your-key-here
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        congiuntura-live app                          │
│                                                                     │
│  RSS Poll (5 min)              Processing Poll (2 min)              │
│       │                              │                              │
│       ▼                              ▼                              │
│  ┌──────────┐   ┌──────┐   ┌──────────────┐   ┌───────────────┐    │
│  │FeedReader│──▶│Dedup │   │   Scraper    │──▶│   Processor   │    │
│  │(RSS/Atom)│   │(hash)│   │(trafilatura) │   │(outlines-     │    │
│  └──────────┘   └──┬───┘   └──────────────┘   │ cascade)      │    │
│                     │                          └───────┬───────┘    │
│                     ▼                                  ▼            │
│              ┌──────────────┐                  ┌──────────────┐     │
│              │press_releases│                  │processed_    │     │
│              │  (raw)       │                  │releases      │     │
│              └──────────────┘                  └──────────────┘     │
│                     │                                  │            │
│                     ▼                                  ▼            │
│              /raw page                           / (main page)     │
│              (secondary)                         (auto-generated    │
│                                                 filters + cards)   │
└─────────────────────────────────────────────────────────────────────┘
```

### Anti-hallucination design

The LLM generates **only** the fields it can reason about. Link, date, and publisher
metadata are copied verbatim from the raw feed **after** generation.

```
LLMExtraction (what the LLM sees)      ProcessedRelease (stored in MongoDB)
┌──────────────────────────┐           ┌──────────────────────────────────┐
│ topic: Literal[...]      │           │ url_hash, url, title  ← from raw │
│ country: Literal[...]    │    +      │ publisher, published  ← from raw │
│ sentiment: Literal[...]  │           │ processing_model      ← from LLM │
│ summary_en: str          │           │ processed_at          ← timestamp│
│ key_figures: str         │           │ topic, country, ...   ← from LLM │
└──────────────────────────┘           └──────────────────────────────────┘
```

### Auto-generated filter UI

The main page introspects the `LLMExtraction` model fields to build filters:

| Pydantic type | Filter control |
|---------------|----------------|
| `Literal[...]` | `<select>` dropdown |
| `str` | Text search |
| `datetime` | Date range |

Change the model → restart → filters update automatically.

---

## Web Interface

- **`/`** (main) — Processed releases with auto-generated filters and enriched cards
- **`/raw`** (secondary) — Raw feeds with publisher + date range search

The ♻ **Reprocess** button on the main page processes all raw items not yet present
in the processed collection (incremental, never re-processes existing items).

---

## Processing Pipeline

1. **RSS poll** (every 5 min) — fetches feeds, deduplicates, stores in `press_releases`
2. **Scrape** — trafilatura extracts main text from the press release page (fallback: feed summary)
3. **LLM extraction** — outlines-cascade generates structured JSON matching `LLMExtraction`
4. **Assembly** — raw fields (url, date, publisher) are copied to the processed document
5. **Storage** — result stored in `processed_releases` with `processing_model` metadata

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.12+ |
| Web framework | FastAPI |
| Frontend | Datastar (SSE) + Pico CSS |
| RSS parsing | feedparser |
| Content scraping | trafilatura |
| LLM extraction | outlines-cascade (Pydantic structured generation) |
| LLM backend | OpenRouter (cloud, OpenAI-compatible) |
| Database | MongoDB (motor async driver) |
| Scheduler | APScheduler |
| Models | Pydantic v2 |
| Dependency management | uv |

---

## Development

### Setup

```bash
uv sync --all-extras
cp .env.example .env
# Edit .env with your MongoDB URL and OpenRouter key
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

## Project Structure

```
congiuntura-live/
├── config/
│   ├── app.toml                 # Application settings (polling, processing)
│   ├── feeds.toml               # RSS feed URLs (user-editable)
│   ├── extraction_model.py      # LLMExtraction Pydantic model (user-editable)
│   └── llm.toml                 # outlines-cascade providers + cascades
├── src/congiuntura_live/
│   ├── __init__.py
│   ├── settings.py              # Config loading (TOML + .env + model loader)
│   ├── models.py                # PressRelease model
│   ├── feed_reader.py           # Async RSS/Atom reader
│   ├── scraper.py               # trafilatura content extraction
│   ├── processor.py             # outlines-cascade pipeline orchestrator
│   ├── repository.py            # Async MongoDB repository (raw + processed)
│   ├── scheduler.py             # FeedPoller + ProcessingPoller (APScheduler)
│   └── app.py                   # FastAPI app + Datastar routes
├── templates/
│   ├── base.html                # Layout with nav (Processed / Raw feeds)
│   ├── index.html               # Main: processed cards + auto-generated filters
│   └── raw.html                 # Secondary: raw feed search
├── tests/
│   ├── conftest.py              # Test fixtures (RSS/Atom samples)
│   ├── test_feed_reader.py      # Feed parsing tests
│   ├── test_dedup.py            # Deduplication tests
│   ├── test_config.py           # Config loading tests
│   ├── test_extraction_model.py # Model loading + introspection tests
│   └── test_scraper.py          # Scraper fallback + live extraction tests
├── Dockerfile                   # Multi-stage build
├── docker-compose.yml           # App + MongoDB
├── pyproject.toml
├── .env.example
├── PLAN.md
└── README.md
```

---

## License

MIT © Luigi Palumbo

---

## Change Log

- **0.2.0** (2025-07-21): LLM processing via outlines-cascade. Structured extraction
  (topic, country, sentiment, EN summary, key figures). Anti-hallucination two-model
  architecture. trafilatura scraping. Auto-generated filter UI. OpenRouter backend.
- **0.1.0** (2025-07-21): Initial release. RSS aggregation from 5 agencies, dedup,
  MongoDB storage, Datastar search UI, Docker Compose deployment.
