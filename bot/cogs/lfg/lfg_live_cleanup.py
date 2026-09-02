from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import asyncpg
import discord
from discord.ext import commands, tasks

from bot.core.branding import GUILDFORGE_COLOR

log = logging.getLogger(__name__)

WARN_AFTER_SECONDS = 7 * 60 * 60
AUTO_CLOSE_AFTER_SECONDS = 30 * 60
LOOP_SECONDS = 5 * 60


def _to_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


class StaleSessionView(discord.ui.View):
    def __init__(
        self,
        bot: commands.Bot,
        session_id: int,
        creator_id: int,
        guild_id: int,
    ) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.session_id = session_id
        self.creator_id = creator_id
        self.guild_id = guild_id
        self._build_buttons()

    def _build_buttons(self) -> None:
        keep = discord.ui.Button(
            label="Continuar aberta",
            style=discord.ButtonStyle.secondary,
            custom_id=f"lfg_stale_keep:{self.session_id}",
        )
        keep.callback = self._keep_callback
        self.add_item(keep)

        close = discord.ui.Button(
            label="Encerrar agora",
            style=discord.ButtonStyle.danger,
            custom_id=f"lfg_stale_close:{self.session_id}",
        )
        close.callback = self._close_callback
        self.add_item(close)

    async def _keep_callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if interaction.user.id != self.creator_id:
            await interaction.response.send_message(
                "Apenas o criador da sessão pode usar este botão.",
                ephemeral=True,
            )
            return

        from bot.services.lfg_repository import (
            get_session_by_id,
            set_warning_sent_at,
        )

        data = await get_session_by_id(self.bot.db_pool, self.session_id)
        if data is None or data["session"]["status"] != "active":
            await interaction.response.edit_message(
                content="❌ Esta sessão já foi encerrada ou cancelada.",
                embed=None,
                view=None,
            )
            return

        await set_warning_sent_at(self.bot.db_pool, self.session_id, None)
        await interaction.response.edit_message(
            content=(
                "✅ Sessão continua aberta. Você será avisado novamente se "
                "ela ficar muito tempo sem encerramento."
            ),
            embed=None,
            view=None,
        )
        log.info(
            "Criador manteve a sessão %s aberta (warning resetado)",
            self.session_id,
        )

    async def _close_callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if interaction.user.id != self.creator_id:
            await interaction.response.send_message(
                "Apenas o criador da sessão pode usar este botão.",
                ephemeral=True,
            )
            return

        from bot.services.lfg_service import LFGService

        await interaction.response.edit_message(
            content="⏳ Encerrando sessão...", view=None
        )
        guild = self.bot.get_guild(self.guild_id)
        if guild is not None:
            await LFGService(self.bot.db_pool).close_silently(
                guild, self.session_id
            )
        try:
            await interaction.edit_original_response(
                content="✅ Sessão encerrada."
            )
        except (discord.NotFound, discord.HTTPException):
            pass
        log.info(
            "Criador encerrou a sessão %s direto pela DM de aviso",
            self.session_id,
        )


class LFGLiveCleanup(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def pool(self) -> Any:
        return self.bot.db_pool

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if not self.stale_loop.is_running():
            self.stale_loop.start()

    async def cog_unload(self) -> None:
        self.stale_loop.cancel()

    @tasks.loop(seconds=LOOP_SECONDS)
    async def stale_loop(self) -> None:
        from bot.services.lfg_repository import (
            list_live_untimed_sessions,
            set_warning_sent_at,
        )

        sessions = await list_live_untimed_sessions(self.pool)
        now = datetime.now(timezone.utc)
        warned = 0
        closed = 0
        for record in sessions:
            session = dict(record)
            session_id = session["id"]

            created_at = _to_dt(session["created_at"])
            if created_at is None:
                continue
            warning_sent_at = _to_dt(session["warning_sent_at"])

            if warning_sent_at is not None:
                elapsed = (now - warning_sent_at).total_seconds()
                if elapsed >= AUTO_CLOSE_AFTER_SECONDS:
                    await _auto_close_session(self.bot, self.pool, session)
                    closed += 1
                continue

            age = (now - created_at).total_seconds()
            if age >= WARN_AFTER_SECONDS:
                await _send_stale_warning(self.bot, session)
                await set_warning_sent_at(
                    self.pool, session_id, datetime.now(timezone.utc)
                )
                warned += 1

        if warned or closed:
            log.info(
                "Auto-limpeza LFG: %d aviso(s) enviado(s), %d sessão(ões) encerrada(s)",
                warned,
                closed,
            )

    @stale_loop.before_loop
    async def _before_stale_loop(self) -> None:
        await self.bot.wait_until_ready()


async def _send_stale_warning(
    bot: commands.Bot,
    session: dict[str, Any],
) -> None:
    session_id = session["id"]
    title = session.get("title", "LFG")

    try:
        user = await bot.fetch_user(session["creator_id"])
    except (discord.NotFound, discord.HTTPException):
        log.warning(
            "Não foi possível obter o criador %s da sessão %s; iniciando contagem de auto-close",
            session["creator_id"],
            session_id,
        )
        return

    created_at = _to_dt(session["created_at"])
    hours_open = 1
    if created_at is not None:
        hours_open = max(
            1, int((datetime.now(timezone.utc) - created_at).total_seconds() // 3600)
        )

    embed = discord.Embed(
        title="Sessão LFG aberta há muito tempo",
        description=(
            f"Sua sessão **{title}** está aberta há **{hours_open} horas** "
            f"sem horário marcado.\n\n"
            f"Clique em **Continuar aberta** para mantê-la, ou **Encerrar agora** "
            f"para fechá-la. Se você não responder em **30 minutos**, a sessão "
            f"será encerrada automaticamente."
        ),
        color=GUILDFORGE_COLOR,
    )
    view = StaleSessionView(
        bot=bot,
        session_id=session_id,
        creator_id=session["creator_id"],
        guild_id=session["guild_id"],
    )

    try:
        await user.send(embed=embed, view=view)
        log.info(
            "Aviso de sessão antiga enviado para %s (sessão %s)",
            user,
            session_id,
        )
    except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
        log.warning(
            "Falha ao enviar aviso de sessão %s para %s: %s",
            session_id,
            user,
            exc,
        )
        view.stop()


async def _auto_close_session(
    bot: commands.Bot,
    pool: asyncpg.Pool,
    session: dict[str, Any],
) -> None:
    from bot.services.lfg_service import LFGService

    guild = bot.get_guild(session["guild_id"])
    if guild is None:
        return
    await LFGService(pool).close_silently(guild, session["id"])
    log.info(
        "Sessão %s encerrada automaticamente (criador não respondeu ao aviso)",
        session["id"],
    )


async def _register_stale_warn_views(bot: commands.Bot) -> None:
    from bot.services.lfg_repository import list_pending_warn_sessions

    pool = getattr(bot, "db_pool", None)
    if pool is None:
        return
    sessions = await list_pending_warn_sessions(pool)
    count = 0
    for record in sessions:
        session = dict(record)
        view = StaleSessionView(
            bot=bot,
            session_id=session["id"],
            creator_id=session["creator_id"],
            guild_id=session["guild_id"],
        )
        bot.add_view(view)
        count += 1
    if count:
        log.info(
            "Registradas %d view(s) persistente(s) de DM de aviso de sessões",
            count,
        )


async def setup(bot: commands.Bot) -> None:
    cog = LFGLiveCleanup(bot)
    await bot.add_cog(cog)
    await _register_stale_warn_views(bot)