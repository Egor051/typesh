from __future__ import annotations

import asyncio
import contextlib
import logging

import discord
from dotenv import load_dotenv

from bot.config import ConfigError, load_settings
from bot.parser import SqstatParser
from bot.state import StateStore
from bot.widget_updater import WidgetUpdater


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(levelname).1s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


async def run_bot() -> None:
    load_dotenv()
    settings = load_settings()
    setup_logging(settings.log_level)

    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    parser = SqstatParser(
        base_url=settings.base_url,
        timeout_seconds=settings.parser_timeout_seconds,
    )
    state_store = StateStore(settings.state_file)
    updater = WidgetUpdater(
        client=client,
        parser=parser,
        state_store=state_store,
        channel_id=settings.channel_id,
        update_interval_seconds=settings.update_interval_seconds,
        heartbeat_edit_interval_seconds=settings.heartbeat_edit_interval_seconds,
        max_backoff_seconds=settings.max_backoff_seconds,
    )

    logger = logging.getLogger(__name__)
    update_task: asyncio.Task[None] | None = None

    @client.event
    async def on_ready() -> None:
        nonlocal update_task

        logger.info(
            "Logged in as %s | update_interval_seconds=%s | heartbeat_edit_interval_seconds=%s | max_backoff_seconds=%s",
            client.user,
            settings.update_interval_seconds,
            settings.heartbeat_edit_interval_seconds,
            settings.max_backoff_seconds,
        )

        if update_task is not None and not update_task.done():
            logger.info("Widget loop already running; skipping duplicate start")
            return

        await updater.initialize()
        update_task = asyncio.create_task(updater.run_forever(), name="widget-updater")

    try:
        await client.start(settings.discord_token)
    finally:
        if update_task is not None:
            update_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await update_task

        await parser.close()


if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except ConfigError as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc