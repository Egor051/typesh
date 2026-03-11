from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from bs4.element import Tag

from .models import ServerSnapshot, WidgetSnapshot

LOGGER = logging.getLogger(__name__)


class SqstatParser:
    """Parser for breaking.proxy.sqstat.ru HTML widgets."""

    def __init__(self, base_url: str, timeout_seconds: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def fetch_html(self) -> str:
        session = requests.Session()
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            )
        }

        response = session.get(self.base_url, timeout=self.timeout_seconds, headers=headers)
        response.raise_for_status()

        html = response.text
        if not html.strip():
            raise ValueError("Received empty HTML response")

        cookie_match = re.search(
            r"document\.cookie\s*=\s*'([^=]+)=([^;]+);",
            html,
            re.IGNORECASE,
        )

        if cookie_match:
            cookie_name = cookie_match.group(1).strip()
            cookie_value = cookie_match.group(2).strip()
            LOGGER.info("Received JS cookie challenge: %s", cookie_name)

            session.cookies.set(cookie_name, cookie_value)
            response = session.get(self.base_url, timeout=self.timeout_seconds, headers=headers)
            response.raise_for_status()
            html = response.text

        if not html.strip():
            raise ValueError("Received empty HTML response after cookie challenge")

        return html

    def fetch_and_parse(self) -> WidgetSnapshot:
        html = self.fetch_html()
        return self.parse(html)

    def parse(self, html: str) -> WidgetSnapshot:
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text("\n", strip=True)

        LOGGER.info("Fetched HTML preview: %s", self._compact_text(html[:3000]))

        raas_snapshot = self._extract_mode_snapshot(text, mode="RAAS/AAS")
        spec_snapshot = self._extract_mode_snapshot(text, mode="SPEC")

        LOGGER.info(
            "Extracted RAAS/AAS -> online=%r map=%r map_url=%r",
            raas_snapshot.online,
            raas_snapshot.map_name,
            raas_snapshot.map_image_url,
        )
        LOGGER.info(
            "Extracted SPEC -> online=%r map=%r map_url=%r",
            spec_snapshot.online,
            spec_snapshot.map_name,
            spec_snapshot.map_image_url,
        )

        return WidgetSnapshot(
            raas_aas=raas_snapshot,
            spec=spec_snapshot,
        )

    def _extract_mode_snapshot(self, page_text: str, mode: str) -> ServerSnapshot:
        lines = [self._compact_text(line) for line in page_text.splitlines()]
        lines = [line for line in lines if line]

        matched_line = ""
        for line in lines:
            upper = line.upper()

            if mode == "RAAS/AAS":
                if "RAAS/AAS" in upper or re.search(r"\bRAAS\b", upper) or re.search(r"\bAAS\b", upper):
                    matched_line = line
                    break
            elif mode == "SPEC":
                if re.search(r"\bSPEC\b", upper):
                    matched_line = line
                    break

        if not matched_line:
            LOGGER.error("%s line was not found in page text", mode)
            return ServerSnapshot(server_name=mode)

        LOGGER.info("Matched %s line: %s", mode, matched_line[:700])

        online = self._extract_online_from_line(matched_line, mode)
        map_name = self._extract_map_from_line(matched_line, mode)

        return ServerSnapshot(
            server_name=mode,
            online=online,
            map_name=map_name,
            map_image_url="",
        )

    def _extract_online_from_line(self, line: str, mode: str) -> str:
        numbers = [int(value) for value in re.findall(r"\b\d{1,3}\b", line)]

        if not numbers:
            return ""

        if mode == "RAAS/AAS":
            filtered = [n for n in numbers if 0 <= n <= 127]
            if filtered:
                return str(max(filtered))
            return str(numbers[0])

        if mode == "SPEC":
            filtered = [n for n in numbers if 0 <= n <= 127]
            if filtered:
                return str(max(filtered))
            return str(numbers[0])

        return ""

    def _extract_map_from_line(self, line: str, mode: str) -> str:
        working = line

        if mode == "RAAS/AAS":
            working = re.split(r"\bRAAS/AAS\b|\bRAAS\b|\bAAS\b", working, maxsplit=1, flags=re.IGNORECASE)[0]
        elif mode == "SPEC":
            working = re.split(r"\bSPEC\b", working, maxsplit=1, flags=re.IGNORECASE)[0]

        working = self._compact_text(working)

        working = re.sub(r"^(Игры|Games)\s+", "", working, flags=re.IGNORECASE)
        working = re.sub(r"\b\d+\b.*$", "", working).strip()

        return working

    @staticmethod
    def _compact_text(value: str) -> str:
        return " ".join(value.split())