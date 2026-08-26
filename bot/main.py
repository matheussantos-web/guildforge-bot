import asyncio
import contextlib
import importlib
import inspect
import json
import logging
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import aiohttp
import asyncpg
import discord
from aiohttp import web
from discord.ext import commands

from bot.config import BOT_NAME, DATABASE_URL, DISCORD_TOKEN, ENVIRONMENT
from bot.core.guild_settings import get_guild_config
from bot.core.migrate import run_migrations

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger(BOT_NAME)

COGS_DIR = pathlib.Path(__file__).parent / "cogs"


async def load_cogs(bot: commands.Bot) -> None:
    for cog_path in sorted(COGS_DIR.rglob("*.py")):
        if cog_path.name.startswith("_"):
            continue
        relative = cog_path.relative_to(COGS_DIR).with_suffix("")
        module_name = "bot.cogs." + ".".join(relative.parts)
        try:
            module = importlib.import_module(module_name)
        except Exception:
            log.exception("Falha ao importar cog %s", module_name)
            continue

        setup_fn = getattr(module, "setup", None)
        if setup_fn is None:
            log.warning("Cog %s não possui setup()", module_name)
            continue
        try:
            result = setup_fn(bot)
            if inspect.isawaitable(result):
                await result
        except Exception:
            log.exception("Falha ao carregar cog %s", module_name)


async def _handle_health(request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def _run_health_server() -> None:
    app = web.Application()
    app.router.add_get("/", _handle_health)
    app.router.add_get("/health", _handle_health)
    port = int(os.getenv("PORT", "8080"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info("Health server ouvindo na porta %s", port)


async def _build_bot(pool: asyncpg.Pool, intents: discord.Intents) -> commands.Bot:
    bot = commands.Bot(
        command_prefix=commands.when_mentioned,
        help_command=None,
        intents=intents,
    )
    bot.db_pool = pool

    @bot.event
    async def on_ready() -> None:
        log.info("%s iniciado como %s (env=%s)", BOT_NAME, bot.user, ENVIRONMENT)

        synced = await bot.tree.sync()
        log.info("%d comando(s) sincronizado(s) globalmente", len(synced))

        bot.loop.create_task(_sync_all_guilds(bot))

    @bot.event
    async def on_guild_join(guild: discord.Guild) -> None:
        try:
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
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

    @bot.tree.command(name="sync_commands", description="Forçar sincronização dos slash commands (admin)")
    @discord.app_commands.default_permissions(manage_guild=True)
    async def sync_commands(interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use em um servidor.", ephemeral=True)
            return
        try:
            bot.tree.copy_global_to(guild=interaction.guild)
            synced = await bot.tree.sync(guild=interaction.guild)
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
            await interaction.response.send_message(
                "❌ Falha ao sincronizar. Verifique as permissões do bot.", ephemeral=True
            )

    @bot.event
    async def on_member_join(member: discord.Member) -> None:
        guild_config = await get_guild_config(pool, member.guild.id)
        if guild_config and guild_config.get("default_role_id"):
            role = member.guild.get_role(guild_config["default_role_id"])
            if role is not None:
                try:
                    await member.add_roles(role, reason="Entrada no servidor")
                except discord.Forbidden:
                    log.warning(
                        "Sem permissão para aplicar cargo padrão em %s",
                        member,
                    )

    await load_cogs(bot)
    return bot


async def _sync_all_guilds(bot: commands.Bot) -> None:
    await bot.wait_until_ready()
    pool: asyncpg.Pool = bot.db_pool
    guilds = list(bot.guilds)
    log.info("Sincronizando comandos em %d guilda(s)...", len(guilds))
    count = 0
    for guild in guilds:
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO guilds (id, name) VALUES ($1, $2) "
                    "ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name",
                    guild.id, guild.name,
                )
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            count += 1
            log.info(
                "  %s (%s): %d comando(s)",
                guild.name, guild.id, len(synced),
            )
        except Exception:
            log.exception(
                "Falha ao sincronizar comandos na guilda %s (%s)",
                guild.name, guild.id,
            )
        await asyncio.sleep(1)
    log.info("Sync concluído: %d/%d guilda(s)", count, len(guilds))


async def _init_connection(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def bootstrap() -> None:
    pool = await asyncpg.create_pool(DATABASE_URL, init=_init_connection)
    try:
        await run_migrations(pool)

        health_task = asyncio.create_task(_run_health_server())

        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = os.getenv("MEMBER_INTENT", "1") != "0"

        bot = await _build_bot(pool, intents)
        try:
            await bot.start(DISCORD_TOKEN)
        except discord.PrivilegedIntentsRequired:
            log.error(
                "A intent privilegiada 'Server Members Intent' não está habilitada no portal "
                "do Discord. O bot vai iniciar com ela desligada, o que limita o auto-role de "
                "novos membros e a varredura de cargos. Habilite em "
                "https://discord.com/developers/applications/ e faça deploy novamente "
                "para ativar esses recursos."
            )
            await bot.close()
            intents.members = False
            bot = await _build_bot(pool, intents)
            await bot.start(DISCORD_TOKEN)
    finally:
        health_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await health_task
        await pool.close()


def main() -> None:
    try:
        asyncio.run(bootstrap())
    except Exception as exc:
        log.critical("Boot falhou (%s); encerrando com código de erro", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
