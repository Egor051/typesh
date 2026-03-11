from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

from .models import ServerSnapshot, WidgetSnapshot

LOGGER = logging.getLogger(__name__)

RAAS_KEYWORDS = ("RAAS/AAS", "RAAS", "AAS")
SPEC_KEYWORDS = ("SPEC",)


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

    def parse(self, html: str) -> WidgetSnapshot:
        soup = BeautifulSoup(html, "html.parser")

        LOGGER.info("Fetched HTML preview: %s", self._compact_text(html[:3000]))

        cards = self._find_server_cards(soup)
        LOGGER.info("Found %s potential cards", len(cards))

        raas_card = self._find_card_by_title(cards, RAAS_KEYWORDS)
        spec_card = self._find_card_by_title(cards, SPEC_KEYWORDS)

        if raas_card is None:
            LOGGER.error("RAAS/AAS card was not found")
            self._log_candidate_blocks(cards, "RAAS/AAS")
        if spec_card is None:
            LOGGER.error("SPEC card was not found")
            self._log_candidate_blocks(cards, "SPEC")

        return WidgetSnapshot(
            raas_aas=self._extract_server_data("RAAS/AAS", raas_card),
            spec=self._extract_server_data("SPEC", spec_card),
        )

    def fetch_and_parse(self) -> WidgetSnapshot:
        html = self.fetch_html()
        return self.parse(html)

    def _find_server_cards(self, soup: BeautifulSoup) -> list[Tag]:
        selectors = [
            ".server-card",
            ".card.server",
            ".server",
            "div[class*='server'][class*='card']",
            "div.card",
            "section",
            "article",
            "div",
        ]

        seen: set[int] = set()
        cards: list[Tag] = []

        for selector in selectors:
            for node in soup.select(selector):
                if not isinstance(node, Tag):
                    continue
                text = node.get_text(" ", strip=True)
                if not text:
                    continue

                normalized = text.upper()
                if "RAAS" in normalized or "AAS" in normalized or "SPEC" in normalized:
                    node_id = id(node)
                    if node_id not in seen:
                        seen.add(node_id)
                        cards.append(node)

        return cards

    def _find_card_by_title(self, cards: list[Tag], keywords: tuple[str, ...]) -> Tag | None:
        for card in cards:
            title = self._extract_card_title(card)
            normalized = title.upper()

            if any(keyword.upper() in normalized for keyword in keywords):
                return card

            full_text = card.get_text(" ", strip=True).upper()
            if keywords == RAAS_KEYWORDS and ("RAAS" in full_text or "AAS" in full_text):
                return card
            if keywords == SPEC_KEYWORDS and "SPEC" in full_text:
                return card

        return None

    def _extract_card_title(self, card: Tag) -> str:
        title_selectors = [
            "h1",
            "h2",
            "h3",
            "h4",
            ".server-title",
            ".card-title",
            "[data-title]",
            "strong",
            "b",
        ]
        for selector in title_selectors:
            node = card.select_one(selector)
            if node:
                text = node.get_text(strip=True)
                if text:
                    return text

        text = card.get_text(" ", strip=True)
        return text[:120]

    def _extract_server_data(self, default_name: str, card: Tag | None) -> ServerSnapshot:
        if card is None:
            return ServerSnapshot(server_name=default_name)

        online = self._extract_online(card)
        map_name, map_url = self._extract_map(card)

        return ServerSnapshot(
            server_name=default_name,
            online=online,
            map_name=map_name,
            map_image_url=map_url,
        )

    def _extract_online(self, card: Tag) -> str:
        text = card.get_text(" ", strip=True)
        compact = " ".join(text.split())

        patterns = [
            r"Онлайн[:\s]+(\d+\s*/\s*\d+)",
            r"Онлайн[:\s]+(\d+)",
            r"Players?[:\s]+(\d+\s*/\s*\d+)",
            r"Players?[:\s]+(\d+)",
        ]

        for pattern in patterns:
            match = re.search(pattern, compact, re.IGNORECASE)
            if match:
                return match.group(1)

        selectors = [
            "[class*='online']",
            "[class*='player']",
            "[class*='stats']",
            ".chart .value",
        ]

        for selector in selectors:
            for node in card.select(selector):
                value = " ".join(node.get_text(" ", strip=True).split())
                if not value:
                    continue

                match = re.search(r"\b\d+\s*/\s*\d+\b", value)
                if match:
                    return match.group(0)

                match = re.search(r"\b\d+\b", value)
                if match:
                    return match.group(0)

        return ""

    def _extract_map(self, card: Tag) -> tuple[str, str]:
        text = " ".join(card.get_text(" ", strip=True).split())

        map_patterns = [
            r"Карта[:\s]+([A-Za-z0-9_\-]+)",
            r"Map[:\s]+([A-Za-z0-9_\-]+)",
        ]

        for pattern in map_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1), ""

        map_img = (
                card.select_one("img[data-type='map']")
                or card.select_one("img[alt*='map' i]")
                or card.select_one("img[title*='map' i]")
        )

        if map_img is not None:
            map_name = (
                    (map_img.get("data-original-title") or "").strip()
                    or (map_img.get("alt") or "").strip()
                    or (map_img.get("title") or "").strip()
            )
            src = (map_img.get("src") or "").strip()
            map_image_url = urljoin(f"{self.base_url}/", src) if src else ""
            return map_name, map_image_url

        return "", ""

    def _log_candidate_blocks(self, cards: list[Tag], label: str) -> None:
        for index, card in enumerate(cards[:10], start=1):
            preview = self._compact_text(card.get_text(" ", strip=True)[:300])
            LOGGER.info("Candidate %s card %s: %s", label, index, preview)

    @staticmethod
    def _compact_text(value: str) -> str:
        return " ".join(value.split())