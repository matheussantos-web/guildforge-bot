import logging
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot.services.roster_service import sync_guild_roster

log = logging.getLogger(__name__)


class RosterSchedulerCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def pool(self) -> Any:
        return self.bot.db_pool

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if not self.roster_loop.is_running():
            self.roster_loop.start()

    async def cog_unload(self) -> None:
        self.roster_loop.cancel()

    @tasks.loop(hours=12)
    async def roster_loop(self) -> None:
        for guild in list(self.bot.guilds):
            try:
                await sync_guild_roster(self.bot, self.pool, guild.id)
            except Exception:
                log.exception("Falha na varredura do roster da guilda %s", guild.id)

    @roster_loop.before_loop
    async def _before_roster_loop(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="sync_roster",
        description="Executa agora a varredura do roster da guilda",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def sync_roster(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use este comando em um servidor.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        result = await sync_guild_roster(self.bot, self.pool, interaction.guild.id)
        await interaction.followup.send(
            f"Varredura concluída: {result['roster']} membro(s) no roster, "
            f"{result['revoked']} cargo(s) de membro revogado(s).",
            ephemeral=True,
        )

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.errors.MissingPermissions):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Você precisa ser administrador do servidor para usar `/sync_roster`.",
                    ephemeral=True,
                )
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RosterSchedulerCog(bot))
