from __future__ import annotations

import logging
from typing import Any

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands

from bot.core.guild_settings import get_guild_config, get_setting
from bot.core.permissions import has_member_role
from bot.services.lfg_repository import (
    create_session,
    get_session_by_id,
    list_active_sessions,
    update_session_message,
)

log = logging.getLogger(__name__)


def _parse_slots(text: str) -> list[dict[str, Any]] | None:
    slots: list[dict[str, Any]] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        segments = [s.strip() for s in part.split(":")]
        if len(segments) < 2:
            return None
        name = segments[0]
        if not name:
            return None
        try:
            limit = int(segments[1])
        except (ValueError, IndexError):
            return None
        if limit < 1:
            return None
        category = segments[2] if len(segments) >= 3 and segments[2] else "Geral"
        slots.append({"role": name, "limit": limit, "category": category})
    return slots if slots else None


class ContentModal(discord.ui.Modal, title="Criar evento de LFG"):
    title_input = discord.ui.TextInput(
        label="Título",
        placeholder="Ex: Dungeon T8, Roaming, Ganking",
        max_length=100,
    )
    description_input = discord.ui.TextInput(
        label="Descrição (opcional)",
        required=False,
        max_length=500,
    )
    event_time_input = discord.ui.TextInput(
        label="Horário (opcional)",
        placeholder="Ex: 20:00 BRT",
        max_length=50,
        required=False,
    )
    slots_config_input = discord.ui.TextInput(
        label="Composição de Vagas (Nome:Qtd:Categoria)",
        placeholder="Ex: Tank:1:Front, Healer:1:Front, DPS:5:DPS",
        style=discord.TextStyle.paragraph,
        max_length=500,
    )

    def __init__(self, pool: asyncpg.Pool, bot: commands.Bot) -> None:
        super().__init__()
        self.pool = pool
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Esta interação só funciona em um servidor.", ephemeral=True
            )
            return

        config = await get_guild_config(self.pool, interaction.guild.id)
        if config is None:
            await interaction.response.send_message(
                "Este servidor ainda não foi configurado. Peça a um administrador "
                "para rodar `/setup` antes de criar eventos.",
                ephemeral=True,
            )
            return

        if not has_member_role(config, interaction.user):
            await interaction.response.send_message(
                "Você precisa ser um membro registrado da guilda para "
                "criar eventos. Use `/registrar` primeiro.",
                ephemeral=True,
            )
            return

        raw = self.slots_config_input.value.strip()
        if not raw:
            await interaction.response.send_message(
                "Informe ao menos uma função. Formato: **Nome:Vagas** ou "
                "**Nome:Vagas:Categoria** (ex: `Tank:1:Front, DPS:5:DPS`).",
                ephemeral=True,
            )
            return

        parsed_slots = _parse_slots(raw)
        if parsed_slots is None:
            await interaction.response.send_message(
                "Formato inválido. Use **Nome:Vagas** ou **Nome:Vagas:Categoria**, "
                "separados por vírgula. Ex: `Tank:1:Front, DPS:5:DPS`",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        session_id = await create_session(
            pool=self.pool,
            guild_id=interaction.guild.id,
            message_id=None,
            channel_id=interaction.channel.id,
            creator_id=interaction.user.id,
            title=self.title_input.value,
            description=self.description_input.value or "",
            event_time=self.event_time_input.value or "",
            slots_config=parsed_slots,
        )

        from bot.cogs.lfg.lfg_embed import build_lfg_embed
        from bot.cogs.lfg.lfg_views import LFGSessionView

        data = await get_session_by_id(self.pool, session_id)
        lfg_role_id = await get_setting(
            self.pool, interaction.guild.id, "lfg_notify_role_id"
        )
        tz_name = await get_setting(
            self.pool, interaction.guild.id, "guild_timezone"
        )
        session = data["session"]
        if lfg_role_id:
            session = dict(session)
            session["lfg_role_id"] = int(lfg_role_id)
        embed = await build_lfg_embed(
            session, [], [], interaction.guild, tz_name
        )
        view = LFGSessionView(session_id, parsed_slots, [])
        msg = await interaction.followup.send(embed=embed, view=view)
        self.bot.add_view(view, message_id=msg.id)

        await update_session_message(self.pool, session_id, msg.id)


class LFGCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def pool(self) -> Any:
        return self.bot.db_pool

    @app_commands.command(
        name="content",
        description="Cria um evento de LFG com vagas para membros entrarem",
    )
    async def content(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use este comando em um servidor.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            ContentModal(pool=self.pool, bot=self.bot)
        )

    async def _register_open_sessions(self) -> None:
        if self.pool is None:
            return
        from bot.services.lfg_repository import get_participants

        sessions = await list_active_sessions(self.pool)
        count = 0
        for session in sessions:
            if session["message_id"] is None:
                continue
            from bot.cogs.lfg.lfg_views import LFGSessionView

            participants = await get_participants(self.pool, session["id"])
            view = LFGSessionView(
                session["id"],
                session.get("slots_config") or [],
                [dict(p) for p in participants],
            )
            self.bot.add_view(view, message_id=session["message_id"])
            count += 1
        if count:
            log.info(
                "Registradas %d view(s) persistente(s) de sessões LFG ativas",
                count,
            )


async def setup(bot: commands.Bot) -> None:
    cog = LFGCog(bot)
    await bot.add_cog(cog)
    await cog._register_open_sessions()
