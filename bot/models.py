from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


@dataclass
class ServerSnapshot:
    server_name: str
    online: str = ""
    map_name: str = ""
    map_image_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
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

    def is_empty(self) -> bool:
        return not any([
            self.raas_aas.online,
            self.raas_aas.map_name,
            self.raas_aas.map_image_url,
            self.spec.online,
            self.spec.map_name,
            self.spec.map_image_url,
        ])
