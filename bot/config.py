from __future__ import annotations

import json
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    discord_token: str
    channel_id: int
    update_interval_seconds: int = 30
    heartbeat_edit_interval_seconds: int = 900
    max_backoff_seconds: int = 300
    parser_timeout_seconds: float = 20.0
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


def _parse_int(name: str, raw: str, *, minimum: int | None = None) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc

    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be at least {minimum}")

    return value


def _parse_float(name: str, raw: str, *, minimum: float | None = None) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number") from exc

    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be at least {minimum}")

    return value


def load_settings() -> Settings:
    file_config = _load_file_config()

    token = _require_value("DISCORD_TOKEN", file_config)
    channel_id = _parse_int("CHANNEL_ID", _require_value("CHANNEL_ID", file_config))

    update_interval_seconds = _parse_int(
        "UPDATE_INTERVAL_SECONDS",
        _get_value("UPDATE_INTERVAL_SECONDS", file_config, "30") or "30",
        minimum=5,
    )
    heartbeat_edit_interval_seconds = _parse_int(
        "HEARTBEAT_EDIT_INTERVAL_SECONDS",
        _get_value("HEARTBEAT_EDIT_INTERVAL_SECONDS", file_config, "900") or "900",
        minimum=update_interval_seconds,
    )
    max_backoff_seconds = _parse_int(
        "MAX_BACKOFF_SECONDS",
        _get_value("MAX_BACKOFF_SECONDS", file_config, "300") or "300",
        minimum=update_interval_seconds,
    )
    parser_timeout_seconds = _parse_float(
        "PARSER_TIMEOUT_SECONDS",
        _get_value("PARSER_TIMEOUT_SECONDS", file_config, "20") or "20",
        minimum=1.0,
    )

    return Settings(
        discord_token=token,
        channel_id=channel_id,
        update_interval_seconds=update_interval_seconds,
        heartbeat_edit_interval_seconds=heartbeat_edit_interval_seconds,
        max_backoff_seconds=max_backoff_seconds,
        parser_timeout_seconds=parser_timeout_seconds,
        base_url=_get_value("BASE_URL", file_config, "https://breaking.proxy.sqstat.ru")
        or "https://breaking.proxy.sqstat.ru",
        state_file=_get_value("STATE_FILE", file_config, "state.json") or "state.json",
        log_level=_get_value("LOG_LEVEL", file_config, "INFO").upper() or "INFO",
    )