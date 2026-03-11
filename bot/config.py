from __future__ import annotations

import json
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
    """Raised when required configuration values are invalid."""


def _load_file_config() -> dict:
    config_path = "config.json"
    if not os.path.exists(config_path):
        return {}

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise ConfigError("config.json contains invalid JSON") from exc

    if not isinstance(data, dict):
        raise ConfigError("config.json must contain a JSON object")

    return data


def _get_value(name: str, file_config: dict, default: str = "") -> str:
    env_value = os.getenv(name, "").strip()
    if env_value:
        return env_value

    file_value = file_config.get(name, default)
    if file_value is None:
        return ""

    return str(file_value).strip()


def _require_value(name: str, file_config: dict) -> str:
    value = _get_value(name, file_config)
    if not value:
        raise ConfigError(f"Configuration value {name} is required")
    return value


def load_settings() -> Settings:
    file_config = _load_file_config()

    token = _require_value("DISCORD_TOKEN", file_config)

    try:
        channel_id = int(_require_value("CHANNEL_ID", file_config))
    except ValueError as exc:
        raise ConfigError("CHANNEL_ID must be an integer") from exc

    interval_raw = _get_value("UPDATE_INTERVAL_SECONDS", file_config, "180") or "180"
    try:
        interval = int(interval_raw)
    except ValueError as exc:
        raise ConfigError("UPDATE_INTERVAL_SECONDS must be an integer") from exc

    if interval < 5:
        raise ConfigError("UPDATE_INTERVAL_SECONDS must be at least 5 seconds")

    return Settings(
        discord_token=token,
        channel_id=channel_id,
        update_interval_seconds=interval,
        base_url=_get_value("BASE_URL", file_config, "https://breaking.proxy.sqstat.ru")
        or "https://breaking.proxy.sqstat.ru",
        state_file=_get_value("STATE_FILE", file_config, "state.json") or "state.json",
        log_level=_get_value("LOG_LEVEL", file_config, "INFO").upper() or "INFO",
    )
