from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

from .models import ServerSnapshot, WidgetSnapshot

LOGGER = logging.getLogger(__name__)


class SqstatParser:
    """Parser for breaking.proxy.sqstat.ru server cards."""

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

        response = session.get(
            self.base_url,
            timeout=self.timeout_seconds,
            headers=headers,
        )
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
            response = session.get(
                self.base_url,
                timeout=self.timeout_seconds,
                headers=headers,
            )
            response.raise_for_status()
            html = response.text

        if not html.strip():
            raise ValueError("Received empty HTML response after cookie challenge")

        return html

    def fetch_and_parse(self) -> WidgetSnapshot:
        return self.parse(self.fetch_html())

    def parse(self, html: str) -> WidgetSnapshot:
        LOGGER.info("HTML length: %s", len(html))
        LOGGER.info("Has RAAS/AAS in raw html: %s", "RAAS/AAS" in html)
        LOGGER.info("Has SPEC in raw html: %s", "SPEC" in html)
        LOGGER.info("Has FW/MDC in raw html: %s", "FW/MDC" in html)
        LOGGER.info("Has block-box in raw html: %s", "block-box" in html)
        LOGGER.info("Has block-box-content in raw html: %s", "block-box-content" in html)
        LOGGER.info("Has data-type=\"map\" in raw html: %s", 'data-type="map"' in html)
        LOGGER.info("Has hidden input in raw html: %s", 'type="hidden"' in html)
        LOGGER.info("Has Текущая in raw html: %s", "Текущая" in html)

        with open("debug_breaking.html", "w", encoding="utf-8") as f:
            f.write(html)

        soup = BeautifulSoup(html, "html.parser")

        LOGGER.info("BeautifulSoup div.block-box count: %s", len(soup.select("div.block-box")))
        LOGGER.info(
            "BeautifulSoup div.block-box-content count: %s",
            len(soup.select("div.block-box-content")),
        )
        LOGGER.info(
            "BeautifulSoup map images count: %s",
            len(soup.select('img[data-type="map"]')),
        )

        cards = self._find_server_cards(soup)
        LOGGER.info("Found %s server cards", len(cards))

        for idx, card in enumerate(cards, start=1):
            preview = self._compact_text(card.get_text(" ", strip=True))
            LOGGER.info("Card %s preview: %s", idx, preview[:300])

        raas_card = self._find_card_by_title(cards, "RAAS/AAS")
        spec_card = self._find_card_by_title(cards, "SPEC")

        # fallback по порядку на странице
        if raas_card is None and len(cards) >= 1:
            raas_card = cards[0]

        if spec_card is None and len(cards) >= 2:
            spec_card = cards[1]

        LOGGER.info("Matched RAAS/AAS card: %s", "yes" if raas_card else "no")
        LOGGER.info("Matched SPEC card: %s", "yes" if spec_card else "no")

        raas_snapshot = self._build_snapshot_from_card("RAAS/AAS", raas_card)
        spec_snapshot = self._build_snapshot_from_card("SPEC", spec_card)

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

    def _find_server_cards(self, soup: BeautifulSoup) -> list[Tag]:
        cards: list[Tag] = []

        for box in soup.select("div.block-box"):
            text = self._compact_text(box.get_text(" ", strip=True)).upper()

            if "ТЕКУЩАЯ" not in text and "CURRENT" not in text:
                continue

            if box.select_one('img[data-type="map"]') is None:
                continue

            cards.append(box)

        return cards

    def _find_card_by_title(self, cards: list[Tag], title: str) -> Tag | None:
        title_upper = title.upper()

        for card in cards:
            text = self._compact_text(card.get_text(" ", strip=True)).upper()
            if title_upper in text:
                return card

        return None

    def _build_snapshot_from_card(
            self,
            server_name: str,
            card: Tag | None,
    ) -> ServerSnapshot:
        if card is None:
            return ServerSnapshot(server_name=server_name)

        content = card.select_one("div.block-box-content") or card

        online = self._extract_online_from_card(content, server_name)
        map_name, map_image_url = self._extract_current_map_from_card(content, server_name)

        return ServerSnapshot(
            server_name=server_name,
            online=online,
            map_name=map_name,
            map_image_url=map_image_url,
        )

    def _extract_online_from_card(self, card: Tag, server_name: str = "") -> str:
        # 1) прямые атрибуты value/data-value/aria-valuenow
        attr_candidates = [
            ('input[type="hidden"]', "value"),
            ('input[value]', "value"),
            ('[data-value]', "data-value"),
            ('[aria-valuenow]', "aria-valuenow"),
            ('[value]', "value"),
        ]

        for selector, attr_name in attr_candidates:
            for node in card.select(selector):
                value = (node.get(attr_name) or "").strip()
                if value.isdigit():
                    LOGGER.info("%s online from %s[%s]=%s", server_name, selector, attr_name, value)
                    return value

        card_html = str(card)

        # 2) regex по html карточки
        regexes = [
            r'type=["\\\']hidden["\\\'][^>]*value=["\\\'](\d{1,3})["\\\']',
            r'aria-valuenow=["\\\'](\d{1,3})["\\\']',
            r'data-value=["\\\'](\d{1,3})["\\\']',
            r'"value"\s*:\s*"?(\\d{1,3})"?',
            r"'value'\s*:\s*'?(\\d{1,3})'?",
            r'"players"\s*:\s*"?(\\d{1,3})"?',
            r'"online"\s*:\s*"?(\\d{1,3})"?',
            r"'players'\s*:\s*'?(\\d{1,3})'?",
            r"'online'\s*:\s*'?(\\d{1,3})'?",
        ]

        for pattern in regexes:
            match = re.search(pattern, card_html, re.IGNORECASE)
            if match:
                value = match.group(1).strip()
                LOGGER.info("%s online from regex %s => %s", server_name, pattern, value)
                return value

        # 3) крайний fallback: число из текста карточки
        text = self._compact_text(card.get_text(" ", strip=True))
        text_match = re.search(r"\b(\d{1,3})\b", text)
        if text_match:
            value = text_match.group(1)
            LOGGER.info("%s online from text fallback => %s", server_name, value)
            return value

        # 4) дамп карточки для дебага
        debug_name = server_name.lower().replace("/", "_").replace(" ", "_") or "unknown"
        with open(f"debug_card_{debug_name}.html", "w", encoding="utf-8") as f:
            f.write(card.prettify())

        LOGGER.warning("%s online not found, dumped HTML to debug_card_%s.html", server_name, debug_name)
        return ""

    def _extract_current_map_from_card(
            self,
            card: Tag,
            server_name: str = "",
    ) -> tuple[str, str]:
        map_img = card.select_one('img[data-type="map"]')

        if map_img is None:
            map_img = card.select_one('img[src*="/maps/"], img[data-src*="/maps/"], img[data-original*="/maps/"]')

        if map_img is None:
            debug_name = server_name.lower().replace("/", "_").replace(" ", "_") or "unknown"
            with open(f"debug_card_{debug_name}.html", "w", encoding="utf-8") as f:
                f.write(card.prettify())
            LOGGER.warning("%s map image not found, dumped HTML to debug_card_%s.html", server_name, debug_name)
            return "", ""

        map_name = (
                (map_img.get("data-original-title") or "")
                or (map_img.get("title") or "")
                or (map_img.get("alt") or "")
                or (map_img.get("data-title") or "")
                or (map_img.get("data-name") or "")
        ).strip()

        src = (
                (map_img.get("src") or "")
                or (map_img.get("data-src") or "")
                or (map_img.get("data-original") or "")
        ).strip()

        map_image_url = urljoin(f"{self.base_url}/", src) if src else ""

        LOGGER.info(
            "%s map extracted -> name=%r src=%r resolved=%r",
            server_name,
            map_name,
            src,
            map_image_url,
        )

        return map_name, map_image_url

    @staticmethod
    def _compact_text(value: str) -> str:
        return " ".join(value.split())