from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import discord

from .embeds import build_embeds
from .models import WidgetSnapshot
from .parser import SqstatParser
from .state import StateStore, snapshot_from_state

LOGGER = logging.getLogger(__name__)


class WidgetUpdater:
    def __init__(
        self,
        client: discord.Client,
        parser: SqstatParser,
        state_store: StateStore,
        channel_id: int,
        update_interval_seconds: int,
    ) -> None:
        self.client = client
        self.parser = parser
        self.state_store = state_store
        self.channel_id = channel_id
        self.update_interval_seconds = update_interval_seconds

        self.message_id: int | None = None
        self.last_snapshot: WidgetSnapshot | None = None
        self.msk = ZoneInfo("Europe/Moscow")

    async def initialize(self) -> None:
        state = self.state_store.load()
        self.message_id = state.get("message_id")
        self.last_snapshot = snapshot_from_state(state)

        channel = self.client.get_channel(self.channel_id)
        if not isinstance(channel, discord.TextChannel):
            fetched = await self.client.fetch_channel(self.channel_id)
            if isinstance(fetched, discord.TextChannel):
                channel = fetched
            else:
                raise RuntimeError(f"Channel {self.channel_id} is not a text channel")

        message = await self._get_or_create_message(channel)
        self.message_id = message.id

        if self.last_snapshot and not self.last_snapshot.is_empty():
            await message.edit(content="🎯 Состояние Серверов BSS", embeds=build_embeds(self.last_snapshot))

        self._persist_state()

    async def run_forever(self) -> None:
        while True:
            try:
                await self.update_once()
            except Exception:
                LOGGER.exception("Widget update cycle failed")
            await asyncio.sleep(self.update_interval_seconds)

    async def update_once(self) -> None:
        try:
            snapshot = await self.parser.fetch_and_parse()
        except Exception:
            LOGGER.exception("Failed to fetch/parse HTML")
            return

        has_same_data = bool(
            self.last_snapshot
            and snapshot.raas_aas.to_dict() == self.last_snapshot.raas_aas.to_dict()
            and snapshot.spec.to_dict() == self.last_snapshot.spec.to_dict()
        )

        snapshot.last_successful_request_at = datetime.now(self.msk)

        if snapshot.is_empty() and self.last_snapshot is not None:
            LOGGER.warning("Received empty snapshot, preserving last successful state")
            return

        channel = await self._get_text_channel()
        message = await self._get_or_create_message(channel)
        await message.edit(content="🎯 Состояние Серверов BSS", embeds=build_embeds(snapshot))

        if has_same_data:
            LOGGER.info("Snapshot data unchanged, updated last successful request time")
        else:
            LOGGER.info("Widget message updated successfully")

        self.last_snapshot = snapshot
        self.message_id = message.id
        self._persist_state()

    async def _get_text_channel(self) -> discord.TextChannel:
        channel = self.client.get_channel(self.channel_id)
        if isinstance(channel, discord.TextChannel):
            return channel
        fetched = await self.client.fetch_channel(self.channel_id)
        if not isinstance(fetched, discord.TextChannel):
            raise RuntimeError(f"Channel {self.channel_id} is not a text channel")
        return fetched

    async def _get_or_create_message(self, channel: discord.TextChannel) -> discord.Message:
        if self.message_id:
            try:
                return await channel.fetch_message(self.message_id)
            except discord.NotFound:
                LOGGER.warning("Stored widget message not found, creating a new one")
            except discord.HTTPException:
                LOGGER.exception("Failed to fetch existing widget message")

        message = await channel.send("🔄 Инициализация виджета серверов...")
        return message

    def _persist_state(self) -> None:
        payload = {
            "channel_id": self.channel_id,
            "message_id": self.message_id,
            "last_snapshot": self.last_snapshot.to_dict() if self.last_snapshot else None,
        }
        self.state_store.save(payload)
