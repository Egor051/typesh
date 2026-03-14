from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import urljoin

from playwright.async_api import (
    Browser,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from .models import ServerSnapshot, WidgetSnapshot

LOGGER = logging.getLogger(__name__)


class SqstatParser:
    """Parser for breaking.proxy.sqstat.ru via rendered DOM (Playwright Async API)."""

    RAAS_ALIASES = ("RAAS/AAS", "RAAS AAS", "RAAS-AAS")
    SPEC_ALIASES = ("SPEC OPS", "SPECOPS", "SPEC-OPS")

    def __init__(self, base_url: str, timeout_seconds: float = 20.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._browser_lock = asyncio.Lock()

    async def close(self) -> None:
        async with self._browser_lock:
            browser = self._browser
            playwright = self._playwright
            self._browser = None
            self._playwright = None

        if browser is not None:
            await browser.close()

        if playwright is not None:
            await playwright.stop()

    async def fetch_and_parse(self) -> WidgetSnapshot:
        cards = await self._fetch_cards()
        return self._build_snapshot(cards)

    async def _ensure_browser(self) -> Browser:
        async with self._browser_lock:
            if self._browser is not None:
                return self._browser

            playwright = await async_playwright().start()
            try:
                browser = await playwright.chromium.launch(headless=True)
            except Exception:
                await playwright.stop()
                raise

            self._playwright = playwright
            self._browser = browser
            return browser

    async def _fetch_cards(self) -> list[dict[str, str]]:
        timeout_ms = int(self.timeout_seconds * 1000)
        browser = await self._ensure_browser()
        page = await browser.new_page(viewport={"width": 1600, "height": 1200})

        try:
            await page.goto(self.base_url, wait_until="domcontentloaded", timeout=timeout_ms)

            try:
                await page.wait_for_load_state("networkidle", timeout=timeout_ms)
            except PlaywrightTimeoutError:
                LOGGER.warning("networkidle timeout; continuing with current DOM")

            try:
                await page.wait_for_selector("div.block-box", timeout=timeout_ms)
            except PlaywrightTimeoutError:
                LOGGER.warning("div.block-box not found before timeout")

            await page.wait_for_timeout(2500)

            raw_cards: list[dict[str, str]] = await page.locator("div.block-box").evaluate_all(
                """
                (els) => els.map((el, idx) => {
                    const text = (el.innerText || "")
                        .replace(/\\s+/g, " ")
                        .trim();

                    const html = el.outerHTML || "";

                    const mapImg =
                        el.querySelector('img[data-type="map"]') ||
                        el.querySelector('img[src*="/maps/"]') ||
                        el.querySelector('img[data-src*="/maps/"]') ||
                        el.querySelector('img[data-original*="/maps/"]');

                    const mapName = (
                        mapImg?.getAttribute("data-original-title") ||
                        mapImg?.getAttribute("title") ||
                        mapImg?.getAttribute("alt") ||
                        mapImg?.getAttribute("data-title") ||
                        mapImg?.getAttribute("data-name") ||
                        ""
                    ).trim();

                    const mapSrc = (
                        mapImg?.getAttribute("src") ||
                        mapImg?.getAttribute("data-src") ||
                        mapImg?.getAttribute("data-original") ||
                        ""
                    ).trim();

                    return {
                        index: String(idx),
                        text,
                        html,
                        map_name: mapName,
                        map_src: mapSrc,
                    };
                })
                """
            )
        finally:
            await page.close()

        cards: list[dict[str, str]] = []
        for card in raw_cards:
            text_upper = (card.get("text") or "").upper()
            html = card.get("html") or ""
            has_current = "ТЕКУЩАЯ" in text_upper or "CURRENT" in text_upper
            has_map = (
                'data-type="map"' in html
                or "/assets/img/maps/" in html
                or bool(card.get("map_src"))
            )

            if has_current and has_map:
                cards.append(card)

        LOGGER.info("Fetched %s eligible server cards", len(cards))
        return cards

    def _build_snapshot(self, cards: list[dict[str, str]]) -> WidgetSnapshot:
        raas_card = self._select_card(
            cards=cards,
            server_name="RAAS/AAS",
            aliases=self.RAAS_ALIASES,
            fallback_index=0,
        )
        spec_card = self._select_card(
            cards=cards,
            server_name="SPEC OPS",
            aliases=self.SPEC_ALIASES,
            fallback_index=1,
        )

        raas_snapshot = self._snapshot_from_card("RAAS/AAS", raas_card)
        spec_snapshot = self._snapshot_from_card("SPEC OPS", spec_card)

        LOGGER.info(
            "Snapshot parsed | cards=%s | RAAS/AAS: online=%s map=%s | SPEC OPS: online=%s map=%s",
            len(cards),
            raas_snapshot.online or "-",
            raas_snapshot.map_name or "-",
            spec_snapshot.online or "-",
            spec_snapshot.map_name or "-",
        )

        return WidgetSnapshot(
            raas_aas=raas_snapshot,
            spec=spec_snapshot,
        )

    def _select_card(
        self,
        cards: list[dict[str, str]],
        server_name: str,
        aliases: tuple[str, ...],
        fallback_index: int,
    ) -> dict[str, str] | None:
        for card in cards:
            searchable = self._card_search_blob(card)
            if any(alias in searchable for alias in aliases):
                return card

        if len(cards) > fallback_index:
            LOGGER.warning(
                "%s card was not identified explicitly; using fallback index %s",
                server_name,
                fallback_index,
            )
            return cards[fallback_index]

        LOGGER.warning("%s card was not found", server_name)
        return None

    @staticmethod
    def _card_search_blob(card: dict[str, str]) -> str:
        return f"{card.get('text', '')} {card.get('html', '')}".upper()

    def _snapshot_from_card(
        self,
        server_name: str,
        card: dict[str, str] | None,
    ) -> ServerSnapshot:
        if not card:
            return ServerSnapshot(server_name=server_name)

        html = card.get("html") or ""
        text = card.get("text") or ""

        online = self._extract_online(html, text, server_name)
        map_name = self._extract_map_name(card, server_name)
        map_image_url = self._extract_map_image_url(card)

        return ServerSnapshot(
            server_name=server_name,
            online=online,
            map_name=map_name,
            map_image_url=map_image_url,
        )

    def _extract_online(self, html: str, text: str, server_name: str) -> str:
        html_patterns = [
            r'type=["\']hidden["\'][^>]*value=["\'](\d{1,3})["\']',
            r'aria-valuenow=["\'](\d{1,3})["\']',
            r'data-value=["\'](\d{1,3})["\']',
            r'data-percent=["\'](\d{1,3})["\']',
            r'"online"\s*:\s*"?(?:\s)?(\d{1,3})"?',
            r'"players"\s*:\s*"?(?:\s)?(\d{1,3})"?',
            r'"value"\s*:\s*"?(?:\s)?(\d{1,3})"?',
            r"'online'\s*:\s*'?(?:\s)?(\d{1,3})'?",
            r"'players'\s*:\s*'?(?:\s)?(\d{1,3})'?",
            r"'value'\s*:\s*'?(?:\s)?(\d{1,3})'?",
        ]

        for pattern in html_patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                return match.group(1)

        text_patterns = [
            r"(?:ОНЛАЙН|ONLINE|PLAYERS?)\D{0,10}(\d{1,3})\b",
            r"\b(\d{1,3})\s*/\s*\d{1,3}\b",
        ]

        normalized_text = re.sub(r"\s+", " ", text).strip()
        for pattern in text_patterns:
            match = re.search(pattern, normalized_text, re.IGNORECASE)
            if match:
                LOGGER.debug("%s online extracted from text fallback", server_name)
                return match.group(1)

        LOGGER.warning("%s: failed to detect online", server_name)
        return ""

    def _extract_map_name(self, card: dict[str, str], server_name: str) -> str:
        map_name = (card.get("map_name") or "").strip()
        if map_name:
            return map_name

        map_src = (card.get("map_src") or "").strip()
        inferred = self._infer_map_name_from_src(map_src)
        if inferred:
            return inferred

        html = card.get("html") or ""
        match = re.search(
            r'/assets/img/maps/([a-z0-9_\-]+)\.(?:jpg|jpeg|png|webp)',
            html,
            re.IGNORECASE,
        )
        if match:
            inferred = self._infer_map_name_from_src(match.group(1))
            if inferred:
                return inferred

        LOGGER.warning("%s: failed to detect map", server_name)
        return ""

    def _extract_map_image_url(self, card: dict[str, str]) -> str:
        map_src = (card.get("map_src") or "").strip()
        if not map_src:
            html = card.get("html") or ""
            match = re.search(
                r'(/assets/img/maps/[a-z0-9_\-]+\.(?:jpg|jpeg|png|webp))',
                html,
                re.IGNORECASE,
            )
            if match:
                map_src = match.group(1)

        if not map_src:
            return ""

        return urljoin(f"{self.base_url}/", map_src)

    def _infer_map_name_from_src(self, src: str) -> str:
        if not src:
            return ""

        src = urljoin(f"{self.base_url}/", src)
        slug = src.rstrip("/").split("/")[-1]
        slug = re.sub(r"\.(jpg|jpeg|png|webp)$", "", slug, flags=re.IGNORECASE)
        slug = slug.replace("-", "_").strip("_")

        if not slug:
            return ""

        parts = [part for part in slug.split("_") if part]
        pretty_parts: list[str] = []

        for part in parts:
            low = part.lower()
            if re.fullmatch(r"v\d+", low):
                pretty_parts.append(low)
            elif low in {"aas", "raas", "tc"}:
                pretty_parts.append(low.upper())
            elif low in {"invasion", "seed", "insurgency", "skirmish"}:
                pretty_parts.append(low.capitalize())
            else:
                pretty_parts.append(low.capitalize())

        return " ".join(pretty_parts)