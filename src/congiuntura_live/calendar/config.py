"""Calendar module configuration — settings + TOML config."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class CalendarSettings(BaseSettings):
    """Calendar secrets — from environment variables."""

    model_config = SettingsConfigDict(extra="ignore")

    ff_proxy_url: str = ""


calendar_settings = CalendarSettings()


class CalendarPollingConfig(BaseModel):
    enabled: bool = True
    # Cron schedule for the daily collection job (UTC).
    cron_hour: int = 7
    cron_minute: int = 0


class CalendarConfig(BaseModel):
    polling: CalendarPollingConfig = CalendarPollingConfig()


def load_calendar_config(config_path: str = "config/calendar.toml") -> CalendarConfig:
    """Load calendar configuration from TOML (falls back to defaults)."""
    path = Path(config_path)
    if not path.exists():
        return CalendarConfig()
    with open(path, "rb") as f:
        data: dict[str, Any] = tomllib.load(f)
    return CalendarConfig(**data)
