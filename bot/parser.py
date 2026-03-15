from __future__ import annotations

import asyncio
import logging
import re
from time import perf_counter
from urllib.parse import urljoin

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    Route,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from .models import ServerSnapshot, WidgetSnapshot

LOGGER = logging.getLogger(__name__)

ONLINE_TEXT_PATTERNS = (
    re.compile(r"(?:ОНЛАЙН|ONLINE|PLAYERS?)\D{0,10}(\d{1,3})\b", re.IGNORECASE),
    re.compile(r"\b(\d{1,3})\s*/\s*\d{1,3}\b", re.IGNORECASE),
)
MAP_FILE_PATTERN = re.compile(r"\.(jpg|jpeg|png|webp)$", re.IGNORECASE)
KNOWN_TRACKER_TOKENS = (
    "google-analytics",
    "googletagmanager",
    "doubleclick",
    "metrika",
    "mc.yandex",
    "facebook",
    "hotjar",
)


class SqstatParser:
    """Parser for breaking.proxy.sqstat.ru via rendered DOM (Playwright Async API)."""

    RAAS_ALIASES = ("RAAS/AAS", "RAAS AAS", "RAAS-AAS")
    SPEC_ALIASES = ("SPEC OPS", "SPECOPS", "SPEC-OPS")

    def __init__(self, base_url: str, timeout_seconds: float = 20.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._browser_lock = asyncio.Lock()

    async def close(self) -> None:
        async with self._browser_lock:
            page = self._page
            context = self._context
            browser = self._browser
            playwright = self._playwright
            self._page = None
            self._context = None
            self._browser = None
            self._playwright = None

        if page is not None and not page.is_closed():
            await page.close()
        if context is not None:
            await context.close()
        if browser is not None:
            await browser.close()
        if playwright is not None:
            await playwright.stop()

    async def fetch_and_parse(self) -> WidgetSnapshot:
        started_at = perf_counter()
        cards = await self._fetch_cards()
        snapshot = self._build_snapshot(cards)
        elapsed_ms = int((perf_counter() - started_at) * 1000)
        LOGGER.info("Fetch/parse finished | cards=%s | elapsed_ms=%s", len(cards), elapsed_ms)
        return snapshot

    async def _ensure_page(self) -> Page:
        async with self._browser_lock:
            if self._page is not None and not self._page.is_closed():
                return self._page

            if self._browser is None:
                playwright = await async_playwright().start()
                try:
                    browser = await playwright.chromium.launch(headless=True)
                except Exception:
                    await playwright.stop()
                    raise
                self._playwright = playwright
                self._browser = browser

            if self._context is None:
                context = await self._browser.new_context(
                    viewport={"width": 1024, "height": 768},
                    service_workers="block",
                )
                await context.route("**/*", self._route_handler)
                self._context = context

            self._page = await self._context.new_page()
            return self._page

    async def _reset_page(self) -> None:
        async with self._browser_lock:
            page = self._page
            self._page = None

        if page is not None and not page.is_closed():
            await page.close()

    async def _route_handler(self, route: Route) -> None:
        request = route.request
        if request.resource_type in {"image", "media", "font"}:
            await route.abort()
            return

        url = request.url.lower()
        if any(token in url for token in KNOWN_TRACKER_TOKENS):
            await route.abort()
            return

        await route.continue_()

    async def _fetch_cards(self) -> list[dict[str, str]]:
        timeout_ms = int(self.timeout_seconds * 1000)
        last_error: Exception | None = None

        for attempt in range(2):
            page = await self._ensure_page()
            try:
                return await self._read_cards_from_page(page, timeout_ms)
            except Exception as exc:
                last_error = exc
                LOGGER.warning(
                    "Page fetch attempt %s failed, recreating page | error=%s",
                    attempt + 1,
                    exc,
                )
                await self._reset_page()

        if last_error is None:
            raise RuntimeError("Failed to fetch cards for an unknown reason")
        raise last_error

    async def _read_cards_from_page(self, page: Page, timeout_ms: int) -> list[dict[str, str]]:
        total_started = perf_counter()

        goto_started = perf_counter()
        await page.goto(self.base_url, wait_until="domcontentloaded", timeout=timeout_ms)
        goto_ms = int((perf_counter() - goto_started) * 1000)

        wait_started = perf_counter()
        await page.wait_for_selector("div.block-box", timeout=timeout_ms)
        try:
            await page.wait_for_function(
                """
                () => {
                    const cards = Array.from(document.querySelectorAll('div.block-box'));
                    if (cards.length < 2) {
                        return false;
                    }
                    return cards.some((el) => /ТЕКУЩАЯ|CURRENT/i.test(el.innerText || ''));
                }
                """,
                timeout=min(timeout_ms, 5000),
            )
        except PlaywrightTimeoutError:
            LOGGER.warning("Server cards were not fully ready before timeout; continuing with current DOM")
        wait_ms = int((perf_counter() - wait_started) * 1000)

        eval_started = perf_counter()
        raw_cards: list[dict[str, str]] = await page.locator("div.block-box").evaluate_all(
            r"""
            (els) => {
                const normalize = (value) => (value || '').replace(/\s+/g, ' ').trim();
                const firstMatch = (text, patterns) => {
                    for (const pattern of patterns) {
                        const match = text.match(pattern);
                        if (match && match[1]) {
                            return String(match[1]).trim();
                        }
                    }
                    return '';
                };

                return els
                    .map((el, idx) => {
                        const text = normalize(el.innerText);
                        const html = el.innerHTML || '';
                        const mapImg =
                            el.querySelector('img[data-type="map"]') ||
                            el.querySelector('img[src*="/maps/"]') ||
                            el.querySelector('img[data-src*="/maps/"]') ||
                            el.querySelector('img[data-original*="/maps/"]');

                        const mapName = normalize(
                            mapImg?.getAttribute('data-original-title') ||
                            mapImg?.getAttribute('title') ||
                            mapImg?.getAttribute('alt') ||
                            mapImg?.getAttribute('data-title') ||
                            mapImg?.getAttribute('data-name') ||
                            ''
                        );

                        const directMapSrc = normalize(
                            mapImg?.getAttribute('src') ||
                            mapImg?.getAttribute('data-src') ||
                            mapImg?.getAttribute('data-original') ||
                            ''
                        );

                        const htmlMapMatch = html.match(/\/assets\/img\/maps\/[a-z0-9_\-]+\.(?:jpg|jpeg|png|webp)/i);
                        const mapSrc = directMapSrc || (htmlMapMatch ? htmlMapMatch[0] : '');

                        const onlineHint = normalize(
                            el.querySelector('input[type="hidden"][value]')?.getAttribute('value') ||
                            el.querySelector('[aria-valuenow]')?.getAttribute('aria-valuenow') ||
                            el.querySelector('[data-value]')?.getAttribute('data-value') ||
                            el.querySelector('[data-percent]')?.getAttribute('data-percent') ||
                            ''
                        );

                        const fallbackOnline = firstMatch(text, [
                            /(?:ОНЛАЙН|ONLINE|PLAYERS?)\D{0,10}(\d{1,3})\b/i,
                            /\b(\d{1,3})\s*\/\s*\d{1,3}\b/i,
                        ]);

                        const hasCurrent = /ТЕКУЩАЯ|CURRENT/i.test(text);
                        const hasMap = Boolean(mapSrc);

                        return {
                            index: String(idx),
                            text,
                            online_hint: onlineHint || fallbackOnline,
                            map_name: mapName,
                            map_src: mapSrc,
                            has_current: hasCurrent ? '1' : '0',
                            has_map: hasMap ? '1' : '0',
                        };
                    })
                    .filter((card) => card.has_current === '1' && card.has_map === '1');
            }
            """
        )
        eval_ms = int((perf_counter() - eval_started) * 1000)
        total_ms = int((perf_counter() - total_started) * 1000)

        LOGGER.info(
            "Fetched %s eligible server cards | goto_ms=%s | wait_ms=%s | eval_ms=%s | total_ms=%s",
            len(raw_cards),
            goto_ms,
            wait_ms,
            eval_ms,
            total_ms,
        )
        return raw_cards

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
        return f"{card.get('text', '')}".upper()

    def _snapshot_from_card(
        self,
        server_name: str,
        card: dict[str, str] | None,
    ) -> ServerSnapshot:
        if not card:
            return ServerSnapshot(server_name=server_name)

        text = card.get("text") or ""
        online_hint = card.get("online_hint") or ""

        online = self._extract_online(text, online_hint, server_name)
        map_name = self._extract_map_name(card, server_name)
        map_image_url = self._extract_map_image_url(card)

        return ServerSnapshot(
            server_name=server_name,
            online=online,
            map_name=map_name,
            map_image_url=map_image_url,
        )

    def _extract_online(self, text: str, online_hint: str, server_name: str) -> str:
        online_hint = online_hint.strip()
        if online_hint.isdigit():
            return online_hint

        normalized_text = re.sub(r"\s+", " ", text).strip()
        for pattern in ONLINE_TEXT_PATTERNS:
            match = pattern.search(normalized_text)
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

        LOGGER.warning("%s: failed to detect map", server_name)
        return ""

    def _extract_map_image_url(self, card: dict[str, str]) -> str:
        map_src = (card.get("map_src") or "").strip()
        if not map_src:
            return ""

        return urljoin(f"{self.base_url}/", map_src)

    def _infer_map_name_from_src(self, src: str) -> str:
        if not src:
            return ""

        src = urljoin(f"{self.base_url}/", src)
        slug = src.rstrip("/").split("/")[-1]
        slug = MAP_FILE_PATTERN.sub("", slug)
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