"""Cog central: eventos do gateway e sincronização de slash commands.

Extrai para um Cog a lógica que antes vivia inline em ``main.py``, separando a
orquestração de bootstrap (quem conecta) do comportamento (o que o bot faz).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands

from bot.config import BOT_NAME, ENVIRONMENT

log = logging.getLogger(__name__)


class CoreCog(commands.Cog):
    """Manipula eventos globais do bot e a sincronização de comandos."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def pool(self) -> Any:
        return self.bot.db_pool

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        log.info("%s iniciado como %s (env=%s)", BOT_NAME, self.bot.user, ENVIRONMENT)
        try:
            synced = await self.bot.tree.sync()
            log.info("%d comando(s) sincronizado(s) globalmente", len(synced))
        except Exception:
            log.exception("Falha ao sincronizar comandos globalmente")

        # Sincronização por guilda roda em background para não bloquear o on_ready.
        asyncio.create_task(self._sync_all_guilds())

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        try:
            self.bot.tree.copy_global_to(guild=guild)
            synced = await self.bot.tree.sync(guild=guild)
            log.info(
                "%d comando(s) sincronizado(s) para nova guilda %s (%s)",
                len(synced),
                guild.name,
                guild.id,
            )
        except Exception:
            log.exception(
                "Falha ao sincronizar comandos na guilda %s (%s)",
                guild.name,
                guild.id,
            )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        from bot.core.guild_settings import get_guild_config

        try:
            guild_config = await get_guild_config(self.pool, member.guild.id)
        except Exception:
            log.exception("Falha ao carregar config de %s para auto-role", member.guild.id)
            return
        if guild_config and guild_config.get("default_role_id"):
            role = member.guild.get_role(guild_config["default_role_id"])
            if role is not None:
                try:
                    await member.add_roles(role, reason="Entrada no servidor")
                except discord.Forbidden:
                    log.warning("Sem permissão para aplicar cargo padrão em %s", member)
                except discord.HTTPException:
                    log.exception("Erro de rede ao aplicar cargo padrão em %s", member)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Libera recursos em memória e re-registra comandos ao sair de uma guilda."""
        from bot.core.guild_settings import drop_guild_cache

        drop_guild_cache(guild.id)
        log.info("Saiu da guilda %s (%s); cache local descartado", guild.name, guild.id)
        try:
            self.bot.tree.clear_commands(guild=guild)
            await self.bot.tree.sync(guild=guild)
        except Exception:
            log.exception(
                "Não foi possível limpar comandos da guilda removida %s (%s)",
                guild.name,
                guild.id,
            )

    @app_commands.command(
        name="sync_commands",
        description="Forçar sincronização dos slash commands (admin)",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def sync_commands(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message(
                "Use em um servidor.", ephemeral=True
            )
            return
        try:
            self.bot.tree.copy_global_to(guild=interaction.guild)
            synced = await self.bot.tree.sync(guild=interaction.guild)
            await interaction.response.send_message(
                f"✅ {len(synced)} comando(s) sincronizado(s).", ephemeral=True
            )
            log.info(
                "%d comando(s) sincronizado(s) manualmente para %s",
                len(synced),
                interaction.guild.name,
            )
        except Exception:
            log.exception("Falha ao sincronizar comandos em %s", interaction.guild.name)
            msg = "❌ Falha ao sincronizar. Verifique as permissões do bot."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)

    async def _sync_all_guilds(self) -> None:
        """Registra cada guilda no banco e sincroniza os comandos, um a um."""
        await self.bot.wait_until_ready()
        pool: asyncpg.Pool = self.bot.db_pool
        guilds = list(self.bot.guilds)
        log.info("Sincronizando comandos em %d guilda(s)...", len(guilds))
        count = 0
        for guild in guilds:
            try:
                async with pool.acquire() as conn:
                    await conn.execute(
                        "INSERT INTO guilds (id, name) VALUES ($1, $2) "
                        "ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name",
                        guild.id,
                        guild.name,
                    )
                self.bot.tree.copy_global_to(guild=guild)
                synced = await self.bot.tree.sync(guild=guild)
                count += 1
                log.info(
                    "  %s (%s): %d comando(s)",
                    guild.name,
                    guild.id,
                    len(synced),
                )
            except Exception:
                log.exception(
                    "Falha ao sincronizar comandos na guilda %s (%s)",
                    guild.name,
                    guild.id,
                )
            await asyncio.sleep(1)
        log.info("Sync concluído: %d/%d guilda(s)", count, len(guilds))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CoreCog(bot))
