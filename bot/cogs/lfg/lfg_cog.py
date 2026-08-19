from __future__ import annotations

import json
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
        label="Vagas (JSON)",
        placeholder='{"Tank":{"limit":1,"category":"Front"},"DPS":{"limit":5,"category":"DPS"}}',
        style=discord.TextStyle.paragraph,
        max_length=1000,
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
            raw = "{}"
        try:
            parsed_slots = json.loads(raw)
        except json.JSONDecodeError:
            await interaction.response.send_message(
                "JSON inválido no campo de vagas. Verifique o formato.",
                ephemeral=True,
            )
            return

        if not isinstance(parsed_slots, dict):
            await interaction.response.send_message(
                "O campo de vagas deve ser um objeto JSON (dict), não uma lista.",
                ephemeral=True,
            )
            return

        for role_name, cfg in parsed_slots.items():
            if not isinstance(cfg, dict):
                await interaction.response.send_message(
                    f"Cada role deve ser um objeto. Erro na role **{role_name}**.",
                    ephemeral=True,
                )
                return
            if "limit" not in cfg or not isinstance(cfg["limit"], int):
                await interaction.response.send_message(
                    f"A role **{role_name}** precisa de um campo `limit` (inteiro).",
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
        embed = build_lfg_embed(
            data["session"],
            [],
            [],
            interaction.guild,
            int(lfg_role_id) if lfg_role_id else None,
        )
        view = LFGSessionView(session_id, parsed_slots)
        msg = await interaction.followup.send(embed=embed, view=view)

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
        sessions = await list_active_sessions(self.pool)
        count = 0
        for session in sessions:
            if session["message_id"] is None:
                continue
            from bot.cogs.lfg.lfg_views import LFGSessionView

            view = LFGSessionView(
                session["id"],
                session.get("slots_config") or {},
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
