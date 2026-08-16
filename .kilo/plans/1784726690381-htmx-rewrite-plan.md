# Plan: Migrate secrets from .env file to environment variables

## Goal

Stop loading secrets from a `.env` file via Pydantic's `env_file` mechanism.
Instead, read all configuration purely from OS environment variables. This is
the standard pattern for cloud deployments (Kubernetes secrets, AWS task
definitions, Docker Compose `environment:` blocks).

The `.env` file remains for local development convenience (loaded by the
shell or `docker-compose --env-file`), but the **application code no longer
reads it directly**.

## Current state

- `settings.py` `Settings` class uses `SettingsConfigDict(env_file=".env")`.
  Pydantic reads `.env` from the working directory.
- `docker-compose.yml` uses `env_file: - .env` which injects vars into the
  container, **and** the app also reads `.env` directly — redundant.
- `.env` (gitignored) contains: `MONGODB_URL`, `MONGODB_DATABASE`,
  `LLM7_API_KEY`, `GROQ_API_KEY`.
- `llm.toml` references `api_key_env = "LLM7_API_KEY"` etc.; `outlines-cascade`
  reads these via `os.environ.get()` — already environment-based, no change.
- `.gitignore` already excludes `.env`.

## Scope

- **In scope:** `settings.py`, `docker-compose.yml`, `.env.example`.
- **Out of scope:** `llm.toml`, `config/` files, application routes, repository,
  processor, scheduler.

## Decisions

- **Four env vars templated:** `MONGODB_URL`, `MONGODB_DATABASE`,
  `LLM7_API_KEY`, `GROQ_API_KEY`. No MongoDB auth credentials (user/password)
  — cloud deploys point `MONGODB_URL` at a managed instance.
- **`docker-compose.yml` keeps `env_file: - .env`** — this is the bridge for
  local dev. The app itself no longer reads `.env`; Docker Compose injects
  the vars into the container environment. For cloud deployments, operators
  replace `env_file` with `environment:` placeholders or a secrets manager.
- **Template `docker-compose.yml` for cloud**: add an `environment:` block
  with placeholder values, commented out, so operators know exactly which
  vars to set.

## Files to modify

### 1. `src/congiuntura_live/settings.py`

Change the `Settings` class to NOT read `.env`:

```python
class Settings(BaseSettings):
    """Secrets and connection strings — loaded from environment variables."""

    model_config = SettingsConfigDict(extra="ignore")

    mongodb_url: str = "mongodb://localhost:27017"
    mongodb_database: str = "congiuntura"
```

Remove `env_file=".env"` and `env_file_encoding="utf-8"`. Pydantic
`BaseSettings` reads from `os.environ` by default — no `env_file` means it
only uses environment variables, which is exactly what we want.

### 2. `docker-compose.yml`

Replace `env_file: - .env` with explicit `environment:` placeholders:

```yaml
  app:
    build: .
    container_name: congiuntura-app
    restart: unless-stopped
    ports:
      - "8000:8000"
    environment:
      - MONGODB_URL=mongodb://mongo:27017
      - MONGODB_DATABASE=congiuntura
      # LLM API keys — set these for your deployment
      - LLM7_API_KEY=${LLM7_API_KEY:-}
      - GROQ_API_KEY=${GROQ_API_KEY:-}
    volumes:
      - ./config:/app/config:ro
      - ./templates:/app/templates:ro
    depends_on:
      mongo:
        condition: service_healthy
```

Key changes:
- `MONGODB_URL` and `MONGODB_DATABASE` have working defaults (point at the
  compose `mongo` service).
- `LLM7_API_KEY` and `GROQ_API_KEY` use `${VAR:-}` syntax — Docker Compose
  interpolates from the host environment or `.env` file (Compose reads `.env`
  automatically for variable substitution). Empty default prevents crash if
  unset.
- **No `env_file` directive** — all vars are explicit.

### 3. `.env.example`

Update to document all four variables and match the new provider set:

```env
# ─── Environment variables (read by the app via os.environ) ───
# For local dev, copy this to .env — Docker Compose reads .env for
# ${VAR} substitution in docker-compose.yml.

# MongoDB connection
MONGODB_URL=mongodb://mongo:27017
MONGODB_DATABASE=congiuntura

# LLM backend API keys (used by outlines-cascade via llm.toml providers)
LLM7_API_KEY=your-llm7-key-here
GROQ_API_KEY=your-groq-key-here
```

## Validation

1. `docker compose down && docker compose up -d --build` — app starts.
2. `curl localhost:8000/health` — returns OK.
3. `docker compose exec app env | grep MONGODB_URL` — env var present in container.
4. Check logs for "LLM processing enabled" — API keys reached outlines-cascade.
5. Confirm the app does NOT have a `.env` file mounted or copied into the
   container (it never was — Docker Compose injects via `environment:`).
