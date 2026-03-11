from __future__ import annotations

from datetime import datetime, timezone

import discord

from .models import ServerSnapshot, WidgetSnapshot


def _server_embed(server: ServerSnapshot, color: int) -> discord.Embed:
    embed = discord.Embed(title=server.server_name, color=color)
    embed.add_field(name="Онлайн", value=server.online or "—", inline=True)
    embed.add_field(name="Карта", value=server.map_name or "—", inline=True)

    if server.map_image_url:
        embed.set_image(url=server.map_image_url)

    return embed


def build_embeds(snapshot: WidgetSnapshot) -> list[discord.Embed]:
    now = datetime.now(timezone.utc)
    footer = f"Последнее успешное обновление: {now.strftime('%Y-%m-%d %H:%M:%S UTC')}"

    raas_embed = _server_embed(snapshot.raas_aas, color=0x2B90D9)
    spec_embed = _server_embed(snapshot.spec, color=0x9B59B6)

    raas_embed.set_footer(text=footer)
    spec_embed.set_footer(text=footer)

    return [raas_embed, spec_embed]
