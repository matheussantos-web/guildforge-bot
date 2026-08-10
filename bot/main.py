import asyncio
import importlib
import inspect
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


async def bootstrap() -> None:
    pool = await asyncpg.create_pool(DATABASE_URL)
    try:
        await run_migrations(pool)

        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        bot = commands.Bot(
            command_prefix=commands.when_mentioned,
            help_command=None,
            intents=intents,
        )
        bot.db_pool = pool

        @bot.event
        async def on_ready() -> None:
            log.info("%s iniciado como %s (env=%s)", BOT_NAME, bot.user, ENVIRONMENT)

            test_guild_id = int(os.getenv("TEST_GUILD_ID", "0") or "0")
            if test_guild_id:
                guild_obj = discord.Object(id=test_guild_id)
                bot.tree.copy_global_to(guild=guild_obj)
                synced = await bot.tree.sync(guild=guild_obj)
                log.info(
                    "%d comando(s) sincronizado(s) para a guilda de teste %s",
                    len(synced),
                    test_guild_id,
                )
            else:
                synced = await bot.tree.sync()
                log.info("%d comando(s) sincronizado(s) globalmente", len(synced))

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
        await asyncio.gather(
            bot.start(DISCORD_TOKEN),
            _run_health_server(),
        )
    finally:
        await pool.close()


def main() -> None:
    try:
        asyncio.run(bootstrap())
    except Exception as exc:
        log.critical("Boot falhou (%s); encerrando com código de erro", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
