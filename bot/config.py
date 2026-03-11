from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    discord_token: str
    channel_id: int
    update_interval_seconds: int = 180
    base_url: str = "https://breaking.proxy.sqstat.ru"
    state_file: str = "state.json"
    log_level: str = "INFO"


class ConfigError(ValueError):
    """Raised when required environment variables are invalid."""


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"Environment variable {name} is required")
    return value


def load_settings() -> Settings:
    token = _require_env("DISCORD_TOKEN")

    try:
        channel_id = int(_require_env("CHANNEL_ID"))
    except ValueError as exc:
        raise ConfigError("CHANNEL_ID must be an integer") from exc

    interval_raw = os.getenv("UPDATE_INTERVAL_SECONDS", "180").strip() or "180"
    try:
        interval = int(interval_raw)
    except ValueError as exc:
        raise ConfigError("UPDATE_INTERVAL_SECONDS must be an integer") from exc

    if interval < 30:
        raise ConfigError("UPDATE_INTERVAL_SECONDS must be at least 30 seconds")

    return Settings(
        discord_token=token,
        channel_id=channel_id,
        update_interval_seconds=interval,
        base_url=os.getenv("BASE_URL", "https://breaking.proxy.sqstat.ru").strip() or "https://breaking.proxy.sqstat.ru",
        state_file=os.getenv("STATE_FILE", "state.json").strip() or "state.json",
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
    )
