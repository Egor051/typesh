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
        response = requests.get(
            self.base_url,
            timeout=self.timeout_seconds,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                )
            },
        )
        response.raise_for_status()
        if not response.text.strip():
            raise ValueError("Received empty HTML response")
        return response.text

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
        selectors = [
            ".chart [class*='online']",
            ".stats [class*='online']",
            "[class*='player'] [class*='value']",
            "[class*='online']",
            ".chart .value",
            "[class*='count']",
            "[class*='stat']",
            "span",
            "div",
        ]

        for selector in selectors:
            for node in card.select(selector):
                text = node.get_text(" ", strip=True)
                if not text:
                    continue

                match = re.search(r"\b\d+\s*/\s*\d+\b", text)
                if match:
                    return match.group(0)

                match = re.search(r"\b\d+\b", text)
                if match:
                    return match.group(0)

        tokens = [token for token in card.get_text(" ", strip=True).split() if any(ch.isdigit() for ch in token)]
        return min(tokens, key=len) if tokens else ""

    def _extract_map(self, card: Tag) -> tuple[str, str]:
        map_img = (
            card.select_one("img[data-type='map']")
            or card.select_one("img[alt*='map' i]")
            or card.select_one("img[title*='map' i]")
            or card.select_one("img")
        )

        if map_img is None:
            LOGGER.warning("Map image was not found for card")
            return "", ""

        map_name = (
            (map_img.get("data-original-title") or "").strip()
            or (map_img.get("alt") or "").strip()
            or (map_img.get("title") or "").strip()
        )
        src = (map_img.get("src") or "").strip()
        map_image_url = urljoin(f"{self.base_url}/", src) if src else ""
        return map_name, map_image_url

    def _log_candidate_blocks(self, cards: list[Tag], label: str) -> None:
        for index, card in enumerate(cards[:10], start=1):
            preview = self._compact_text(card.get_text(" ", strip=True)[:300])
            LOGGER.info("Candidate %s card %s: %s", label, index, preview)

    @staticmethod
    def _compact_text(value: str) -> str:
        return " ".join(value.split())