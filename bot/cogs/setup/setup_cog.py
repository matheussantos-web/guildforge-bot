from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from bot.core.guild_settings import (
    get_guild_config,
    get_setting,
    set_setting,
    upsert_guild_config,
)
from bot.services.albion_api_service import AlbionAPIError, search_guild_by_name


class SetupCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def pool(self) -> Any:
        return self.bot.db_pool

    @app_commands.command(
        name="setup",
        description="Configura a guilda (cargo de membro, canal de log, pontos e guilda do Albion)",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        member_role="Cargo que marca membro completo",
        log_channel="Canal para logs do bot",
        points_per_hour="Pontos por hora em call",
        guild_name="Nome da guilda no Albion Online (igual ao do jogo)",
        default_role="Cargo aplicado a quem entra no servidor",
        lfg_notify_role="Cargo mencionado ao criar eventos LFG",
    )
    async def setup(
        self,
        interaction: discord.Interaction,
        member_role: discord.Role | None = None,
        log_channel: discord.TextChannel | None = None,
        points_per_hour: int | None = None,
        guild_name: str | None = None,
        default_role: discord.Role | None = None,
        lfg_notify_role: discord.Role | None = None,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Este comando só pode ser usado em um servidor.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        if points_per_hour is not None and points_per_hour < 0:
            await interaction.followup.send(
                "`points_per_hour` não pode ser negativo.",
                ephemeral=True,
            )
            return

        guild = interaction.guild
        config = await get_guild_config(self.pool, guild.id)
        exists = config is not None

        fields = {}
        if member_role is not None:
            fields["member_role_id"] = member_role.id
        if log_channel is not None:
            fields["log_channel_id"] = log_channel.id
        if points_per_hour is not None:
            fields["points_per_hour_voice"] = points_per_hour
        if default_role is not None:
            fields["default_role_id"] = default_role.id

        lfg_notify_handled = False
        if lfg_notify_role is not None:
            await set_setting(
                self.pool, guild.id, "lfg_notify_role_id", str(lfg_notify_role.id)
            )
            lfg_notify_handled = True

        if guild_name is not None:
            guild_name = guild_name.strip()
            if not guild_name:
                await interaction.followup.send(
                    "`guild_name` não pode ficar em branco.",
                    ephemeral=True,
                )
                return
            try:
                albion_guild = await search_guild_by_name(guild_name)
            except AlbionAPIError as exc:
                await interaction.followup.send(
                    f"Não foi possível consultar a API do Albion: {exc}",
                    ephemeral=True,
                )
                return
            if albion_guild is None:
                await interaction.followup.send(
                    f"Não encontrei a guilda **{guild_name}** na API do Albion. "
                    "Confira se o nome está igual ao do jogo.",
                    ephemeral=True,
                )
                return
            fields["albion_guild_id"] = albion_guild["id"]
            fields["albion_guild_name"] = albion_guild["name"]

        if not exists and not fields and not lfg_notify_handled:
            await interaction.followup.send(
                "Este servidor ainda não foi configurado. Use `/setup` informando ao menos "
                "`member_role` (cargo de membro) — o registro e os demais módulos dependem disso.",
                ephemeral=True,
            )
            return

        if fields:
            await upsert_guild_config(self.pool, guild.id, name=guild.name, **fields)

        final = await get_guild_config(self.pool, guild.id) or {}
        await interaction.followup.send(
            embed=self._build_summary(guild, final, lfg_notify_role),
            ephemeral=True,
        )

    @staticmethod
    def _build_summary(guild: discord.Guild, config: dict[str, Any], lfg_role: discord.Role | None = None) -> discord.Embed:
        member_role_id = config.get("member_role_id")
        log_channel_id = config.get("log_channel_id")
        default_role_id = config.get("default_role_id")

        member_role = guild.get_role(member_role_id) if member_role_id else None
        log_channel = guild.get_channel(log_channel_id) if log_channel_id else None
        default_role = guild.get_role(default_role_id) if default_role_id else None

        embed = discord.Embed(
            title="Configuração da guilda",
            description=f"Configuração atual de **{guild.name}**",
            color=discord.Color.green(),
        )
        embed.add_field(
            name="Cargo de membro",
            value=member_role.mention if member_role else "Não definido",
            inline=True,
        )
        embed.add_field(
            name="Canal de log",
            value=log_channel.mention if log_channel else "Não definido",
            inline=True,
        )
        embed.add_field(
            name="Pontos por hora em call",
            value=str(config.get("points_per_hour_voice", 10)),
            inline=True,
        )
        embed.add_field(
            name="Guilda Albion",
            value=config.get("albion_guild_name") or "Não configurada",
            inline=True,
        )
        embed.add_field(
            name="Cargo padrão",
            value=default_role.mention if default_role else "Não definido",
            inline=True,
        )
        lfg_notify_role_id = config.get("lfg_notify_role_id")
        lfg_notify = lfg_role or (guild.get_role(lfg_notify_role_id) if lfg_notify_role_id else None)
        embed.add_field(
            name="Cargo LFG",
            value=lfg_notify.mention if lfg_notify else "Não definido",
            inline=True,
        )
        return embed

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.errors.MissingPermissions):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Você precisa ser administrador do servidor para usar `/setup`.",
                    ephemeral=True,
                )
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SetupCog(bot))
