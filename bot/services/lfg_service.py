"""Serviço de negócio LFG (Looking For Group).

Concentra as operações e regras de LFG que pertencem à camada de serviço —
persistência, validação de slots/fila e reconstrução do estado de uma sessão —
separadas da camada de apresentação (views em ``bot/cogs/lfg/lfg_views.py``).

Os métodos que mudam o estado da sessão serializam o acesso por sessão com
``bot.core.locks.lock_for``, garantindo release mesmo em caso de exceção.
"""

from __future__ import annotations

import logging
from typing import Any

import asyncpg
import discord

from bot.core.guild_settings import get_setting
from bot.core.locks import lock_for
from bot.services.lfg_repository import (
    get_session_by_id,
    remove_participant,
    update_session_status,
    upsert_participant,
)

log = logging.getLogger(__name__)

MAX_QUEUE_SIZE = 25


def parse_slots(text: str) -> list[dict[str, Any]] | None:
    """Interpreta a entrada de composição de vagas do usuário.

    Formato: ``Nome:Vagas`` ou ``Nome:Vagas:Categoria``, separados por vírgula.
    Retorna ``None`` se a entrada for inválida.
    """
    slots: list[dict[str, Any]] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        segments = [s.strip() for s in part.split(":")]
        if len(segments) < 2:
            return None
        name = segments[0]
        if not name:
            return None
        try:
            limit = int(segments[1])
        except (ValueError, IndexError):
            return None
        if limit < 1:
            return None
        category = segments[2] if len(segments) >= 3 and segments[2] else "Geral"
        slots.append({"role": name, "limit": limit, "category": category})
    return slots if slots else None


def _find_role_cfg(
    slots_config: list[dict[str, Any]], role_name: str
) -> dict[str, Any] | None:
    for entry in slots_config:
        if isinstance(entry, dict) and entry.get("role") == role_name:
            return entry
    return None


class LFGService:
    """Regras de negócio e persistência de sessões LFG."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def join_role(
        self, guild_id: int, user_id: int, session_id: int, role: str
    ) -> tuple[bool, str]:
        """Inscreve ``user_id`` na função ``role`` de uma sessão ativa.

        Retorna ``(ok, mensagem)``. ``ok=False`` indica uma regra de negócio
        violada (sessão inativa, função cheia, já inscrito, etc.).
        """
        async with lock_for(guild_id, session_id):
            data = await get_session_by_id(self.pool, session_id)
            if data is None or data["session"]["status"] != "active":
                return False, "Esta sessão não está mais ativa."
            slots_config = data["session"].get("slots_config") or []
            role_cfg = _find_role_cfg(slots_config, role)
            if not isinstance(role_cfg, dict):
                return False, "Função inválida."

            limit = role_cfg.get("limit", 1)
            participants = data["participants"]
            current_count = len(
                [p for p in participants if p.get("role") == role]
            )
            already_in = next(
                (p for p in participants if p["user_id"] == user_id), None
            )
            if already_in and already_in.get("role") == role:
                return False, "Você já está inscrito nesta função."
            if current_count >= limit:
                return False, f"Não há vagas disponíveis para **{role}**."

            await upsert_participant(self.pool, session_id, user_id, role)
            return True, "Você confirmou sua vaga."

    async def join_queue(
        self, guild_id: int, user_id: int, session_id: int
    ) -> tuple[bool, str]:
        """Coloca ``user_id`` na fila de espera da sessão."""
        from bot.services.lfg_repository import queue_participant

        async with lock_for(guild_id, session_id):
            data = await get_session_by_id(self.pool, session_id)
            if data is None or data["session"]["status"] != "active":
                return False, "Esta sessão não está mais ativa."

            already_in = next(
                (p for p in data["participants"] if p["user_id"] == user_id),
                None,
            )
            if already_in and already_in.get("role") is not None:
                return (
                    False,
                    "Você já está inscrito em uma função. "
                    "Saia primeiro para entrar na fila.",
                )
            if already_in and already_in.get("role") is None:
                return False, "Você já está na fila."

            current_queue = sum(
                1 for p in data["participants"] if p.get("role") is None
            )
            if current_queue >= MAX_QUEUE_SIZE:
                return (
                    False,
                    f"A fila está lotada ({MAX_QUEUE_SIZE} participantes "
                    "máximos). Tente novamente mais tarde.",
                )

            await queue_participant(self.pool, session_id, user_id)
            return True, "Você entrou na fila de espera."

    async def leave(
        self, guild_id: int, user_id: int, session_id: int
    ) -> tuple[bool, str]:
        """Remove ``user_id`` da sessão (função ou fila)."""
        async with lock_for(guild_id, session_id):
            removed_role = await remove_participant(
                self.pool, session_id, user_id
            )
            if removed_role is None:
                return False, "Você não está participando desta sessão."
            return True, "Você saiu da sessão."

    async def close(
        self,
        guild_id: int,
        user_id: int,
        session_id: int,
        *,
        is_admin: bool,
    ) -> tuple[bool, str]:
        """Encerra uma sessão ativa, validando que ``user_id`` é criador/staff."""
        async with lock_for(guild_id, session_id):
            data = await get_session_by_id(self.pool, session_id)
            if data is None:
                return False, "Sessão não encontrada."
            if not self._check_event_admin(data["session"], user_id, is_admin):
                return False, "Apenas o criador ou staff pode encerrar esta sessão."
            if data["session"]["status"] != "active":
                return False, "Esta sessão já foi encerrada."
            await update_session_status(self.pool, session_id, "closed")
            return True, ""

    async def close_silently(
        self,
        guild: discord.Guild,
        session_id: int,
    ) -> dict | None:
        """Encerra uma sessão sem interação do usuário (auto-limpeza / DM).

        Marca o status como ``closed`` e atualiza a mensagem original no canal
        com os botões desabilitados. Retorna o dict completo da sessão, ou None.
        """
        async with lock_for(guild.id, session_id):
            data = await get_session_by_id(self.pool, session_id)
            if data is None or data["session"]["status"] != "active":
                return data
            await update_session_status(self.pool, session_id, "closed")

        result = await self.rebuild_view(guild, session_id)
        if result is not None:
            embed, content, view, _ = result
            for item in view.children:
                item.disabled = True
            channel = guild.get_channel(data["session"]["channel_id"])
            if channel is not None:
                try:
                    original_msg = await channel.fetch_message(
                        data["session"]["message_id"]
                    )
                    await original_msg.edit(
                        content=content, embed=embed, view=view
                    )
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass

        return data

    async def rebuild_view(
        self,
        guild: discord.Guild,
        session_id: int,
    ) -> tuple[discord.Embed, str, Any, dict] | None:
        """Reconstrói embed + view de uma sessão a partir do estado persistido."""
        from bot.cogs.lfg.lfg_embed import build_lfg_embed
        from bot.cogs.lfg.lfg_views import LFGSessionView

        data = await get_session_by_id(self.pool, session_id)
        if data is None:
            return None

        lfg_role_id = await self._fetch_lfg_role_id(guild.id)
        session = dict(data["session"])
        if lfg_role_id:
            session["lfg_role_id"] = lfg_role_id
        tz_name = await self._fetch_guild_timezone(guild.id)
        guild_display_name = await self._fetch_guild_display_name(guild.id)

        embed, content = await build_lfg_embed(
            session,
            data["participants"],
            data["pending_claims"],
            guild,
            tz_name,
            guild_display_name,
        )
        slots_config = data["session"].get("slots_config") or []
        view = LFGSessionView(session_id, slots_config, data["participants"])
        return embed, content, view, data

    async def _fetch_lfg_role_id(self, guild_id: int) -> int | None:
        val = await get_setting(self.pool, guild_id, "lfg_notify_role_id")
        return int(val) if val else None

    async def _fetch_guild_timezone(self, guild_id: int) -> str | None:
        return await get_setting(self.pool, guild_id, "guild_timezone")

    async def _fetch_guild_display_name(self, guild_id: int) -> str | None:
        return await get_setting(self.pool, guild_id, "guild_display_name")

    @staticmethod
    def _check_event_admin(session: dict, user_id: int, is_admin: bool) -> bool:
        return session["creator_id"] == user_id or is_admin
