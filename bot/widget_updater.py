from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from time import perf_counter
from zoneinfo import ZoneInfo

import discord

from .embeds import build_embeds
from .models import WidgetSnapshot
from .parser import SqstatParser
from .state import StateStore, snapshot_from_state

LOGGER = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")


class WidgetUpdater:
    def __init__(
        self,
        client: discord.Client,
        parser: SqstatParser,
        state_store: StateStore,
        channel_id: int,
        update_interval_seconds: int,
        heartbeat_edit_interval_seconds: int,
        max_backoff_seconds: int,
    ) -> None:
        self.client = client
        self.parser = parser
        self.state_store = state_store
        self.channel_id = channel_id
        self.update_interval_seconds = update_interval_seconds
        self.heartbeat_edit_interval_seconds = heartbeat_edit_interval_seconds
        self.max_backoff_seconds = max_backoff_seconds

        self.message_id: int | None = None
        self.last_snapshot: WidgetSnapshot | None = None
        self._channel: discord.TextChannel | None = None
        self._message: discord.Message | None = None
        self._last_heartbeat_edit_at: datetime | None = None
        self._last_saved_payload_serialized: str | None = None

    async def initialize(self) -> None:
        state = self.state_store.load()
        self.message_id = state.get("message_id")
        self.last_snapshot = snapshot_from_state(state)
        self._last_heartbeat_edit_at = (
            self.last_snapshot.last_successful_request_at if self.last_snapshot else None
        )

        channel = await self._get_text_channel()
        message = await self._get_or_create_message(channel)
        self.message_id = message.id

        if self.last_snapshot and not self.last_snapshot.is_empty():
            await self._edit_message(message, self.last_snapshot)

        self._persist_state(force=True)
        LOGGER.info(
            "Widget initialized | channel_id=%s | message_id=%s | heartbeat_interval_seconds=%s",
            self.channel_id,
            self.message_id,
            self.heartbeat_edit_interval_seconds,
        )

    async def run_forever(self) -> None:
        consecutive_failures = 0

        while True:
            cycle_started = perf_counter()
            status = await self.update_once()
            if status == "failure":
                consecutive_failures += 1
            else:
                consecutive_failures = 0

            sleep_seconds = self._get_sleep_seconds(consecutive_failures)
            cycle_ms = int((perf_counter() - cycle_started) * 1000)
            LOGGER.info(
                "Widget cycle finished | status=%s | elapsed_ms=%s | next_sleep_seconds=%s",
                status,
                cycle_ms,
                sleep_seconds,
            )
            await asyncio.sleep(sleep_seconds)

    async def update_once(self) -> str:
        fetch_started = perf_counter()
        try:
            snapshot = await self.parser.fetch_and_parse()
        except Exception:
            LOGGER.exception("Failed to fetch/parse HTML")
            return "failure"
        fetch_ms = int((perf_counter() - fetch_started) * 1000)

        snapshot = snapshot.with_timestamp(datetime.now(MSK))
        if snapshot.is_empty():
            LOGGER.warning(
                "Received empty snapshot, preserving current widget state | fetch_ms=%s",
                fetch_ms,
            )
            return "noop"

        previous_snapshot = self.last_snapshot
        self.last_snapshot = snapshot
        content_changed = not snapshot.same_content(previous_snapshot)
        heartbeat_due = self._is_heartbeat_due(snapshot.last_successful_request_at)

        if not content_changed and not heartbeat_due:
            LOGGER.info(
                "Widget heartbeat skipped: data unchanged and heartbeat not due | fetch_ms=%s",
                fetch_ms,
            )
            return "noop"

        message = await self._get_or_create_message(await self._get_text_channel())
        publish_reason = "state-changed" if content_changed else "heartbeat"

        publish_started = perf_counter()
        try:
            edited_message = await self._edit_message(message, snapshot)
        except discord.NotFound:
            LOGGER.warning("Widget message disappeared, recreating it")
            self._message = None
            self.message_id = None
            message = await self._get_or_create_message(await self._get_text_channel())
            edited_message = await self._edit_message(message, snapshot)
        except discord.HTTPException:
            LOGGER.exception("Failed to publish widget update")
            return "failure"
        publish_ms = int((perf_counter() - publish_started) * 1000)

        self._message = edited_message
        self.message_id = edited_message.id
        self._last_heartbeat_edit_at = snapshot.last_successful_request_at

        persist_started = perf_counter()
        state_saved = self._persist_state(force=False)
        persist_ms = int((perf_counter() - persist_started) * 1000)

        LOGGER.info(
            "Widget published | reason=%s | fetch_ms=%s | publish_ms=%s | persist_ms=%s | state_saved=%s",
            publish_reason,
            fetch_ms,
            publish_ms,
            persist_ms,
            state_saved,
        )
        return publish_reason

    def _is_heartbeat_due(self, current_timestamp: datetime | None) -> bool:
        if current_timestamp is None:
            return False
        if self._last_heartbeat_edit_at is None:
            return True
        delta_seconds = (current_timestamp - self._last_heartbeat_edit_at).total_seconds()
        return delta_seconds >= self.heartbeat_edit_interval_seconds

    async def _get_text_channel(self) -> discord.TextChannel:
        if self._channel is not None:
            return self._channel

        channel = self.client.get_channel(self.channel_id)
        if isinstance(channel, discord.TextChannel):
            self._channel = channel
            return channel

        fetched = await self.client.fetch_channel(self.channel_id)
        if not isinstance(fetched, discord.TextChannel):
            raise RuntimeError(f"Channel {self.channel_id} is not a text channel")

        self._channel = fetched
        return fetched

    async def _get_or_create_message(self, channel: discord.TextChannel) -> discord.Message:
        if self._message is not None:
            return self._message

        if self.message_id:
            try:
                self._message = await channel.fetch_message(self.message_id)
                return self._message
            except discord.NotFound:
                LOGGER.warning("Stored widget message not found, creating a new one")
            except discord.HTTPException:
                LOGGER.exception("Failed to fetch existing widget message")

        self._message = await channel.send("🔄 Инициализация виджета серверов...")
        self.message_id = self._message.id
        return self._message

    async def _edit_message(
        self,
        message: discord.Message,
        snapshot: WidgetSnapshot,
    ) -> discord.Message:
        edited = await message.edit(
            content="🎯 Состояние Серверов BSS",
            embeds=build_embeds(snapshot),
        )
        return edited if isinstance(edited, discord.Message) else message

    def _persist_state(self, *, force: bool) -> bool:
        payload = {
            "channel_id": self.channel_id,
            "message_id": self.message_id,
            "last_snapshot": self.last_snapshot.to_dict() if self.last_snapshot else None,
        }
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        if not force and serialized == self._last_saved_payload_serialized:
            return False

        saved = self.state_store.save(payload)
        if saved:
            self._last_saved_payload_serialized = serialized
        return saved

    def _get_sleep_seconds(self, consecutive_failures: int) -> int:
        if consecutive_failures <= 0:
            return self.update_interval_seconds

        capped_failures = min(consecutive_failures - 1, 4)
        backoff_seconds = self.update_interval_seconds * (2**capped_failures)
        return min(backoff_seconds, self.max_backoff_seconds)