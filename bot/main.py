import asyncio
import contextlib
import importlib
import inspect
import json
import logging
import os
import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import asyncpg
import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands

from bot.config import BOT_NAME, DATABASE_URL, DISCORD_TOKEN, ENVIRONMENT
from bot.core.db import set_bot
from bot.core.logging import setup_logging
from bot.core.migrate import run_migrations
from bot.services.albion_api_service import close_albion_api

setup_logging(environment=ENVIRONMENT, level="INFO")
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


def _install_error_handlers(bot: commands.Bot) -> None:
    """Instala handlers globais para impedir crash em interações/erros não tratados."""

    @bot.tree.error
    async def _on_app_command_error(
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        # Barra global: qualquer erro não capturado por um cog é logado e
        # reportado com segurança, sem derrubar o bot.
        if isinstance(error, app_commands.errors.CommandNotFound):
            return
        log.exception("Erro no app command /%s: %s", interaction.command, error)
        message = "Ocorreu um erro inesperado. Tente novamente."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except (discord.HTTPException, discord.Forbidden):
            pass

    @bot.event
    async def on_error(event: str, *args: Any, **kwargs: Any) -> None:
        log.exception("Erro não tratado no evento '%s'", event)

    @bot.event
    async def on_disconnect() -> None:
        log.warning("Conexão com o gateway Discord perdida; aguardando reconexão...")


def _install_exception_handler() -> None:
    """Evita que exceções em tasks/loops derrubem o processo em produção."""
    loop = asyncio.get_running_loop()

    def _handler(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        exc = context.get("exception")
        message = context.get("message", "Exceção não tratada na event loop")
        if exc is not None:
            log.error("Unhandled exception na event loop: %s [%s]", message, type(exc).__name__)
        else:
            log.error("Unhandled error na event loop: %s", message)

    loop.set_exception_handler(_handler)


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
    set_bot(bot)

    _install_error_handlers(bot)
    await load_cogs(bot)
    return bot


async def _init_connection(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def bootstrap() -> None:
    _install_exception_handler()
    pool = await asyncpg.create_pool(DATABASE_URL, init=_init_connection)
    health_task = None
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
        if health_task is not None:
            health_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await health_task
        await close_albion_api()
        await pool.close()


def main() -> None:
    try:
        asyncio.run(bootstrap())
    except Exception as exc:
        log.critical("Boot falhou (%s); encerrando com código de erro", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
