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
        soup = BeautifulSoup(html, "html.parser")

        cards = self._find_server_cards(soup)
        LOGGER.info("Found %s server cards", len(cards))

        for idx, card in enumerate(cards, start=1):
            preview = self._compact_text(card.get_text(" ", strip=True))
            LOGGER.info("Card %s preview: %s", idx, preview[:200])

        raas_card = self._find_card_by_title(cards, "RAAS/AAS")
        spec_card = self._find_card_by_title(cards, "SPEC")

        # fallback по порядку карточек на странице:
        # 1-я обычно RAAS/AAS, 2-я обычно SPEC
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

            # Отбираем именно карточки серверов, у которых есть блок "Текущая"
            if "ТЕКУЩАЯ" not in text and "CURRENT" not in text:
                continue

            content = box.select_one("div.block-box-content")
            if content is None:
                continue

            # Онлайн на странице хранится в hidden input value="..."
            if not content.select_one('input[type="hidden"][value]'):
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

        content = card.select_one("div.block-box-content")
        if content is None:
            return ServerSnapshot(server_name=server_name)

        online = self._extract_online_from_card(content)
        map_name, map_image_url = self._extract_current_map_from_card(content)

        return ServerSnapshot(
            server_name=server_name,
            online=online,
            map_name=map_name,
            map_image_url=map_image_url,
        )

    def _extract_online_from_card(self, card: Tag) -> str:
        hidden = card.select_one('input[type="hidden"][value]')
        if hidden:
            value = (hidden.get("value") or "").strip()
            if value.isdigit():
                return value

        # fallback: если value не нашли, пробуем взять первое число из текста карточки
        text = self._compact_text(card.get_text(" ", strip=True))
        match = re.search(r"\b(\d{1,3})\b", text)
        return match.group(1) if match else ""

    def _extract_current_map_from_card(self, card: Tag) -> tuple[str, str]:
        # На скрине текущая карта лежит в левом блоке .col-xs-7
        map_img = card.select_one('.col-xs-7 img[data-type="map"]')

        # fallback на случай изменения вёрстки
        if map_img is None:
            map_img = card.select_one('img[data-type="map"]')

        if map_img is None:
            return "", ""

        map_name = (
            (map_img.get("data-original-title") or "")
            or (map_img.get("title") or "")
            or (map_img.get("alt") or "")
        ).strip()

        src = (map_img.get("src") or "").strip()
        map_image_url = urljoin(f"{self.base_url}/", src) if src else ""

        return map_name, map_image_url

    @staticmethod
    def _compact_text(value: str) -> str:
        return " ".join(value.split())