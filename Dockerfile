# ── Stage 1: build ──────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy project files (README.md is required by hatchling: pyproject.toml references it)
COPY pyproject.toml uv.lock* README.md ./
COPY src/ src/
COPY config/ config/
COPY templates/ templates/

# Install dependencies (no editable, production build)
RUN uv sync --frozen --no-dev || uv sync --no-dev

# ── Stage 2: runtime ────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# Copy the virtualenv from builder
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Copy application code
COPY --from=builder /app/src /app/src
COPY --from=builder /app/config /app/config
COPY --from=builder /app/templates /app/templates

# Python environment
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "congiuntura_live.app:app", "--host", "0.0.0.0", "--port", "8000"]
