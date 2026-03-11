from __future__ import annotations

import asyncio
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
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


async def run_bot() -> None:
    load_dotenv()
    settings = load_settings()
    setup_logging(settings.log_level)

    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    parser = SqstatParser(base_url=settings.base_url)
    state_store = StateStore(settings.state_file)
    updater = WidgetUpdater(
        client=client,
        parser=parser,
        state_store=state_store,
        channel_id=settings.channel_id,
        update_interval_seconds=settings.update_interval_seconds,
    )

    @client.event
    async def on_ready() -> None:
        logging.getLogger(__name__).info("Logged in as %s", client.user)
        await updater.initialize()
        client.loop.create_task(updater.run_forever())

    await client.start(settings.discord_token)


if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except ConfigError as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc
