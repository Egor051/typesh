from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .models import ServerSnapshot, WidgetSnapshot

LOGGER = logging.getLogger(__name__)


class StateStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            with self.path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            LOGGER.exception("Failed to load state file: %s", self.path)
            return {}

    def save(self, state: dict[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception:
            LOGGER.exception("Failed to save state file: %s", self.path)


def snapshot_from_state(data: dict[str, Any]) -> WidgetSnapshot | None:
    widget_data = data.get("last_snapshot")
    if not isinstance(widget_data, dict):
        return None

    def _server(key: str, label: str) -> ServerSnapshot:
        raw = widget_data.get(key, {})
        if not isinstance(raw, dict):
            return ServerSnapshot(server_name=label)
        return ServerSnapshot(
            server_name=raw.get("server_name") or label,
            online=raw.get("online") or "",
            map_name=raw.get("map_name") or "",
            map_image_url=raw.get("map_image_url") or "",
        )

    return WidgetSnapshot(
        raas_aas=_server("raas_aas", "RAAS/AAS"),
        spec=_server("spec", "SPEC"),
    )
