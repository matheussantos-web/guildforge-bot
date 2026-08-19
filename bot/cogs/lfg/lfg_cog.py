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
    @app_commands.describe(
        title="Título do evento",
        description="Descrição (opcional)",
        event_time="Horário do evento (opcional)",
        slots_config=(
            "Configuração de vagas em JSON. Ex: "
            '{"Tank": {"limit": 1, "category": "Frontline"}, '
            '"DPS": {"limit": 5, "category": "DPS"}}'
        ),
    )
    async def content(
        self,
        interaction: discord.Interaction,
        title: str,
        description: str = "",
        event_time: str = "",
        slots_config: str = "{}",
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use este comando em um servidor.",
                ephemeral=True,
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
                "usar este comando. Use `/registrar` primeiro.",
                ephemeral=True,
            )
            return

        try:
            parsed_slots = json.loads(slots_config)
        except json.JSONDecodeError:
            await interaction.response.send_message(
                "JSON inválido em `slots_config`. Verifique o formato.",
                ephemeral=True,
            )
            return

        if not isinstance(parsed_slots, dict):
            await interaction.response.send_message(
                "`slots_config` deve ser um objeto JSON (dict), não uma lista.",
                ephemeral=True,
            )
            return

        for role_name, cfg in parsed_slots.items():
            if not isinstance(cfg, dict):
                await interaction.response.send_message(
                    f"Cada role em `slots_config` deve ser um objeto. "
                    f"Erro na role **{role_name}**.",
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
            title=title,
            description=description,
            event_time=event_time,
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
                json.loads(session["slots_config"])
                if isinstance(session["slots_config"], str)
                else session["slots_config"],
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
