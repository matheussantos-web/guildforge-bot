from __future__ import annotations

import logging
from typing import Any

import asyncpg
import discord
from discord.ext import commands, tasks

log = logging.getLogger(__name__)


class LFGClaimChecker(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def pool(self) -> Any:
        return self.bot.db_pool

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if not self.claim_loop.is_running():
            self.claim_loop.start()

    async def cog_unload(self) -> None:
        self.claim_loop.cancel()

    @tasks.loop(seconds=20)
    async def claim_loop(self) -> None:
        from bot.services.lfg_repository import get_expired_unresolved_claims

        expired = await get_expired_unresolved_claims(self.pool)
        for claim in expired:
            await _resolve_expired_claim(self.bot, self.pool, claim)

    @claim_loop.before_loop
    async def _before_claim_loop(self) -> None:
        await self.bot.wait_until_ready()


async def _resolve_expired_claim(
    bot: commands.Bot,
    pool: asyncpg.Pool,
    claim: asyncpg.Record,
) -> None:
    from bot.core.locks import get_lock
    from bot.core.guild_settings import get_setting
    from bot.services.lfg_repository import (
        get_session_by_id,
        remove_participant,
        resolve_pending_claim,
    )
    from bot.cogs.lfg.lfg_embed import build_lfg_embed
    from bot.cogs.lfg.lfg_views import LFGSessionView

    session_id = claim["session_id"]
    user_id = claim["user_id"]

    data = await get_session_by_id(pool, session_id)
    if data is None:
        return
    session = data["session"]
    guild_id = session["guild_id"]

    async with get_lock(guild_id, session_id):
        await resolve_pending_claim(pool, session_id, user_id)

        removed_role = await remove_participant(pool, session_id, user_id)
        if removed_role is None:
            return

        channel = bot.get_channel(session["channel_id"])
        if channel is None:
            return

        try:
            user = await bot.fetch_user(user_id)
            dm_msg = (
                f"Seu tempo de confirmação para **{claim['role']}** na sessão "
                f"**{session['title']}** expirou. Você foi movido para o fim da fila."
            )
            await user.send(dm_msg)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass

        participants = data["participants"]
        lfg_role_id = await get_setting(
            pool, guild_id, "lfg_notify_role_id"
        )
        if lfg_role_id:
            session = dict(session)
            session["lfg_role_id"] = int(lfg_role_id)
        embed = await build_lfg_embed(
            session,
            participants,
            data["pending_claims"],
            channel.guild,
        )
        slots_config = session.get("slots_config") or {}
        view = LFGSessionView(session_id, slots_config, participants)

        try:
            msg = await channel.fetch_message(session["message_id"])
            await msg.edit(embed=embed, view=view)
            bot.add_view(view, message_id=session["message_id"])
        except (discord.NotFound, discord.HTTPException):
            pass

        queued = [p for p in participants if p.get("role") is None]
        if queued:
            next_in_queue = queued[0]
            await _notify_next_in_queue(
                bot, pool, session, next_in_queue, claim["role"]
            )


async def _notify_next_in_queue(
    bot: commands.Bot,
    pool: asyncpg.Pool,
    session: dict,
    next_participant: dict,
    role: str,
) -> None:
    from bot.services.lfg_repository import create_pending_claim
    from datetime import datetime, timedelta, timezone

    user_id = next_participant["user_id"]
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=2)
    await create_pending_claim(pool, session["id"], user_id, role, expires_at)

    try:
        user = await bot.fetch_user(user_id)
        channel = bot.get_channel(session["channel_id"])
        if channel is None:
            return
        msg_text = (
            f"<@{user_id}>, uma vaga para **{role}** abriu! "
            f"Você tem **2 minutos** para clicar em **Jogar** e escolher esta função. "
            f"Caso contrário, a vaga passará para o próximo da fila."
        )
        await channel.send(msg_text)
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        pass


async def resolve_startup_claims(
    bot: commands.Bot, pool: asyncpg.Pool
) -> None:
    from bot.services.lfg_repository import get_expired_unresolved_claims

    expired = await get_expired_unresolved_claims(pool)
    if expired:
        log.info(
            "Resolvendo %d claim(s) expirado(s) durante downtime", len(expired)
        )
    for claim in expired:
        await _resolve_expired_claim(bot, pool, claim)


async def setup(bot: commands.Bot) -> None:
    cog = LFGClaimChecker(bot)
    await bot.add_cog(cog)
    await resolve_startup_claims(bot, cog.pool)
