import asyncio
import importlib
import inspect
import logging
import pathlib
import sys

import asyncpg
import discord
from discord.ext import commands

from bot.config import BOT_NAME, DATABASE_URL, DISCORD_TOKEN, ENVIRONMENT
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


async def bootstrap() -> None:
    pool = await asyncpg.create_pool(DATABASE_URL)
    try:
        await run_migrations(pool)

        intents = discord.Intents.default()
        bot = commands.Bot(command_prefix="!", intents=intents)
        bot.db_pool = pool

        @bot.event
        async def on_ready() -> None:
            log.info("%s iniciado como %s (env=%s)", BOT_NAME, bot.user, ENVIRONMENT)

        await load_cogs(bot)
        await bot.start(DISCORD_TOKEN)
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
