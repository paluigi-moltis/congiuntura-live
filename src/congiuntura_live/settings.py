"""Application configuration loaded from TOML files and .env."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

# ─── Secrets from .env ─────────────────────────────────────────


class Settings(BaseSettings):
    """Secrets and connection strings — loaded from .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    mongodb_url: str = "mongodb://localhost:27017"
    mongodb_database: str = "congiuntura"


# ─── App config from config/app.toml ──────────────────────────


class PollingConfig(BaseModel):
    interval_minutes: int = 5


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000


class LoggingConfig(BaseModel):
    level: str = "INFO"


class AppConfig(BaseModel):
    """Application configuration loaded from config/app.toml."""

    polling: PollingConfig = PollingConfig()
    server: ServerConfig = ServerConfig()
    logging: LoggingConfig = LoggingConfig()


def load_app_config(config_path: str = "config/app.toml") -> AppConfig:
    """Load application configuration from a TOML file."""
    path = Path(config_path)
    if not path.exists():
        return AppConfig()
    with open(path, "rb") as f:
        data: dict[str, Any] = tomllib.load(f)
    return AppConfig(**data)


# ─── Feeds config from config/feeds.toml ──────────────────────


class FeedConfig(BaseModel):
    label: str
    url: str


class AgencyConfig(BaseModel):
    name: str
    language: str
    feeds: list[FeedConfig]


def load_feeds_config(config_path: str = "config/feeds.toml") -> dict[str, AgencyConfig]:
    """Load feeds configuration from a TOML file.

    Returns a dict mapping agency slug → AgencyConfig.
    """
    path = Path(config_path)
    with open(path, "rb") as f:
        data: dict[str, Any] = tomllib.load(f)

    agencies: dict[str, AgencyConfig] = {}
    for slug, raw in data.items():
        agencies[slug] = AgencyConfig(**raw)
    return agencies
