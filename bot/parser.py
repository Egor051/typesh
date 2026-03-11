from __future__ import annotations

import logging
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
        response = requests.get(self.base_url, timeout=self.timeout_seconds)
        response.raise_for_status()
        if not response.text.strip():
            raise ValueError("Received empty HTML response")
        return response.text

    def parse(self, html: str) -> WidgetSnapshot:
        soup = BeautifulSoup(html, "html.parser")
        cards = self._find_server_cards(soup)
        LOGGER.debug("Found %s potential cards", len(cards))

        raas_card = self._find_card_by_title(cards, RAAS_KEYWORDS)
        spec_card = self._find_card_by_title(cards, SPEC_KEYWORDS)

        if raas_card is None:
            LOGGER.error("RAAS/AAS card was not found")
        if spec_card is None:
            LOGGER.error("SPEC card was not found")

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
        ]
        for selector in selectors:
            cards = [node for node in soup.select(selector) if isinstance(node, Tag)]
            if cards:
                return cards
        return []

    def _find_card_by_title(self, cards: list[Tag], keywords: tuple[str, ...]) -> Tag | None:
        for card in cards:
            title = self._extract_card_title(card)
            if not title:
                continue
            normalized = title.upper()
            if all(keyword.upper() in normalized for keyword in keywords):
                return card
            # fallback for RAAS/AAS where title can contain either token
            if keywords == RAAS_KEYWORDS and any(token in normalized for token in ("RAAS", "AAS")):
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
        ]
        for selector in title_selectors:
            node = card.select_one(selector)
            if node:
                text = node.get_text(strip=True)
                if text:
                    return text
        return card.get_text(" ", strip=True)[:120]

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
        # Keep selectors local to the current card to avoid global fragile parsing.
        selectors = [
            ".chart [class*='online']",
            ".stats [class*='online']",
            "[class*='player'] [class*='value']",
            "[class*='online']",
            ".chart .value",
        ]

        for selector in selectors:
            node = card.select_one(selector)
            if node:
                text = node.get_text(" ", strip=True)
                if text:
                    return text

        # Final fallback: find shortest numeric-like token in card text.
        tokens = [token for token in card.get_text(" ", strip=True).split() if any(ch.isdigit() for ch in token)]
        return min(tokens, key=len) if tokens else ""

    def _extract_map(self, card: Tag) -> tuple[str, str]:
        map_img = card.select_one("img[data-type='map']")
        if map_img is None:
            LOGGER.warning("Map image with data-type='map' was not found for card")
            return "", ""

        map_name = (map_img.get("data-original-title") or "").strip()
        src = (map_img.get("src") or "").strip()
        map_image_url = urljoin(f"{self.base_url}/", src) if src else ""
        return map_name, map_image_url
