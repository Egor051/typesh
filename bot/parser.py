from __future__ import annotations

import logging
import re

import requests
from bs4 import BeautifulSoup

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
        return self.parse(self.fetch_html())

    def parse(self, html: str) -> WidgetSnapshot:
        soup = BeautifulSoup(html, "html.parser")
        page_text = soup.get_text("\n", strip=True)
        compact_text = self._compact_text(page_text)

        LOGGER.info("Fetched HTML preview: %s", self._compact_text(html[:3000]))

        game_lines = self._extract_game_lines(compact_text)
        LOGGER.info("Found %s parsed game lines", len(game_lines))
        for idx, line in enumerate(game_lines[:5], start=1):
            LOGGER.info("Game line %s: %s", idx, line)

        raas_line = self._find_line_for_mode(game_lines, "RAAS/AAS")
        spec_line = self._find_line_for_mode(game_lines, "SPEC")

        LOGGER.info("Matched RAAS/AAS line: %s", raas_line or "<not found>")
        LOGGER.info("Matched SPEC line: %s", spec_line or "<not found>")

        raas_snapshot = self._build_snapshot("RAAS/AAS", raas_line)
        spec_snapshot = self._build_snapshot("SPEC", spec_line)

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

    def _extract_game_lines(self, text: str) -> list[str]:
        if "Игры" in text:
            text = text.split("Игры", 1)[1]
        elif "Games" in text:
            text = text.split("Games", 1)[1]

        text = self._compact_text(text)

        pattern = re.compile(
            r"([A-Za-z0-9/][A-Za-z0-9\s#()/_\-]*?\b\d{9,10}\b\s*-\s*\b\d{9,10}\b\s+[A-Za-z0-9][A-Za-z0-9\s/_\-]*?\s+\d{1,3}\s+[A-Za-z0-9][A-Za-z0-9\s/_\-]*?\s+\d{1,3})",
            re.IGNORECASE,
        )

        matches = []
        for match in pattern.finditer(text):
            line = self._compact_text(match.group(1))
            if len(line) >= 20:
                matches.append(line)

        return matches

    def _find_line_for_mode(self, lines: list[str], mode: str) -> str:
        for line in lines:
            upper = line.upper()
            if mode == "RAAS/AAS" and "RAAS/AAS" in upper:
                return line
            if mode == "SPEC" and re.search(r"\bSPEC\b", upper):
                return line
        return ""

    def _build_snapshot(self, mode: str, line: str) -> ServerSnapshot:
        if not line:
            return ServerSnapshot(server_name=mode)

        online = self._extract_online_from_line(line)
        map_name = self._extract_map_from_line(line, mode)

        return ServerSnapshot(
            server_name=mode,
            online=online,
            map_name=map_name,
            map_image_url="",
        )

    def _extract_online_from_line(self, line: str) -> str:
        after_time = re.split(r"\b\d{9,10}\b\s*-\s*\b\d{9,10}\b", line, maxsplit=1)
        if len(after_time) < 2:
            return ""

        tail = after_time[1]
        nums = [int(x) for x in re.findall(r"\b\d{1,3}\b", tail)]
        nums = [n for n in nums if 0 <= n <= 127]

        if not nums:
            return ""

        return str(max(nums))

    def _extract_map_from_line(self, line: str, mode: str) -> str:
        if mode == "RAAS/AAS":
            parts = re.split(r"\bRAAS/AAS\b", line, maxsplit=1, flags=re.IGNORECASE)
        else:
            parts = re.split(r"\bSPEC\b", line, maxsplit=1, flags=re.IGNORECASE)

        if not parts:
            return ""

        map_name = self._compact_text(parts[0])

        map_name = re.sub(r"\s+#\d+\s*$", "", map_name).strip()

        return map_name

    @staticmethod
    def _compact_text(value: str) -> str:
        return " ".join(value.split())