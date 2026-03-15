from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ServerSnapshot:
    server_name: str
    online: str = ""
    map_name: str = ""
    map_image_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "server_name": self.server_name,
            "online": self.online,
            "map_name": self.map_name,
            "map_image_url": self.map_image_url,
        }

    def content_key(self) -> tuple[str, str, str, str]:
        return (
            self.server_name,
            self.online,
            self.map_name,
            self.map_image_url,
        )


@dataclass(frozen=True)
class WidgetSnapshot:
    raas_aas: ServerSnapshot
    spec: ServerSnapshot
    last_successful_request_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "raas_aas": self.raas_aas.to_dict(),
            "spec": self.spec.to_dict(),
            "last_successful_request_at": (
                self.last_successful_request_at.isoformat()
                if self.last_successful_request_at
                else None
            ),
        }

    def same_content(self, other: "WidgetSnapshot | None") -> bool:
        if other is None:
            return False
        return self.content_key() == other.content_key()

    def content_key(self) -> tuple[tuple[str, str, str, str], tuple[str, str, str, str]]:
        return (self.raas_aas.content_key(), self.spec.content_key())

    def with_timestamp(self, timestamp: datetime | None) -> "WidgetSnapshot":
        return WidgetSnapshot(
            raas_aas=self.raas_aas,
            spec=self.spec,
            last_successful_request_at=timestamp,
        )

    def is_empty(self) -> bool:
        return not any(
            [
                self.raas_aas.online,
                self.raas_aas.map_name,
                self.raas_aas.map_image_url,
                self.spec.online,
                self.spec.map_name,
                self.spec.map_image_url,
            ]
        )