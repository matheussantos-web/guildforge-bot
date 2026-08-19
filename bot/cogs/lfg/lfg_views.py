from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

import asyncpg
import discord

from bot.core.branding import get_role_emoji

log = logging.getLogger(__name__)


def _build_select_options(
    slots_config: dict[str, Any],
    participants: list[dict],
) -> list[discord.SelectOption]:
    counts: dict[str, int] = defaultdict(int)
    for p in participants:
        role = p.get("role")
        if role:
            counts[role] += 1

    options: list[discord.SelectOption] = []
    for role_name, cfg in slots_config.items():
        if not isinstance(cfg, dict):
            continue
        limit = cfg.get("limit", 1)
        count = counts.get(role_name, 0)
        emoji = get_role_emoji(role_name)
        full = count >= limit
        options.append(
            discord.SelectOption(
                label=f"{emoji} {role_name} ({count}/{limit})",
                value=role_name,
                description=(
                    "Função cheia — entre na fila"
                    if full
                    else f"Garantir vaga como {role_name}"
                ),
                emoji=emoji,
            )
        )

    queue_count = sum(1 for p in participants if p.get("role") is None)
    options.append(
        discord.SelectOption(
            label=f"⌛ Entrar na Fila ({queue_count})",
            value="__queue__",
            description="Entrar na fila de espera",
        )
    )

    return options if options else [
        discord.SelectOption(label="Nenhuma role", value="__none__")
    ]


class LFGSessionView(discord.ui.View):
    def __init__(
        self,
        session_id: int,
        slots_config: dict[str, Any] | None = None,
        participants: list[dict] | None = None,
    ) -> None:
        super().__init__(timeout=None)
        self.session_id = session_id
        cfg = slots_config or {}
        parts = participants or []
        if cfg:
            options = _build_select_options(cfg, parts)
            self.add_item(
                LFGRoleSelect(session_id, options)
            )
        self.add_item(
            LFGSessionButton(
                session_id, "leave", "Sair", discord.ButtonStyle.secondary
            )
        )
        self.add_item(
            LFGSessionButton(
                session_id, "close", "Encerrar", discord.ButtonStyle.danger
            )
        )


class LFGRoleSelect(discord.ui.Select):
    def __init__(
        self,
        session_id: int,
        options: list[discord.SelectOption],
    ) -> None:
        super().__init__(
            placeholder="🎯 Escolha sua função ou entre na fila...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"lfg_role_select:{session_id}",
        )
        self.session_id = session_id

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.values[0] == "__none__":
            await interaction.response.send_message(
                "Nenhuma função disponível.", ephemeral=True
            )
            return
        if self.values[0] == "__queue__":
            await _handle_lfg_session_interaction(
                interaction, self.session_id, "queue"
            )
        else:
            await _handle_lfg_session_interaction(
                interaction, self.session_id, "join_role", self.values[0]
            )


class LFGSessionButton(discord.ui.Button):
    def __init__(
        self,
        session_id: int,
        action: str,
        label: str,
        style: discord.ButtonStyle,
    ) -> None:
        super().__init__(
            custom_id=f"lfg_{action}:{session_id}",
            label=label,
            style=style,
        )
        self.session_id = session_id
        self.action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        await _handle_lfg_session_interaction(
            interaction, self.session_id, self.action
        )


def _inject_lfg_role(session: dict, lfg_role_id: int | None) -> dict:
    if lfg_role_id:
        session = dict(session)
        session["lfg_role_id"] = lfg_role_id
    return session


async def _handle_lfg_session_interaction(
    interaction: discord.Interaction,
    session_id: int,
    action: str,
    role_value: str | None = None,
) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            "Esta interação só funciona em um servidor.",
            ephemeral=True,
        )
        return

    pool: asyncpg.Pool = interaction.client.db_pool
    try:
        if action == "join_role":
            await _join_with_role(pool, interaction, session_id, role_value)
        elif action == "queue":
            await _join_queue(pool, interaction, session_id)
        elif action == "leave":
            await _leave_session(pool, interaction, session_id)
        elif action == "close":
            await _close_session(pool, interaction, session_id)
    except Exception:
        log.exception(
            "Erro ao processar interação LFG '%s' da sessão %s",
            action,
            session_id,
        )
        if interaction.response.is_done():
            await interaction.followup.send(
                "Algo deu errado ao processar sua interação.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "Algo deu errado ao processar sua interação.",
                ephemeral=True,
            )


async def _join_with_role(
    pool: asyncpg.Pool,
    interaction: discord.Interaction,
    session_id: int,
    role: str,
) -> None:
    from bot.core.guild_settings import get_guild_config, get_setting
    from bot.core.locks import get_lock
    from bot.core.permissions import has_member_role
    from bot.services.lfg_repository import (
        get_session_by_id,
        upsert_participant,
    )
    from bot.cogs.lfg.lfg_embed import build_lfg_embed
    from bot.cogs.lfg.lfg_views import LFGSessionView

    config = await get_guild_config(pool, interaction.guild_id)
    if not has_member_role(config, interaction.user):
        await interaction.response.send_message(
            "Você precisa ter o cargo de membro da guilda.",
            ephemeral=True,
        )
        return

    async with get_lock(interaction.guild_id, session_id):
        data = await get_session_by_id(pool, session_id)
        if data is None or data["session"]["status"] != "active":
            await interaction.response.send_message(
                "Esta sessão não está mais ativa.", ephemeral=True
            )
            return

        slots_config = data["session"].get("slots_config") or {}
        role_cfg = slots_config.get(role)
        if not isinstance(role_cfg, dict):
            await interaction.response.send_message(
                "Função inválida.", ephemeral=True
            )
            return

        limit = role_cfg.get("limit", 1)
        participants = data["participants"]
        current_count = len(
            [p for p in participants if p.get("role") == role]
        )
        already_in = next(
            (p for p in participants if p["user_id"] == interaction.user.id),
            None,
        )
        if already_in and already_in.get("role") == role:
            await interaction.response.send_message(
                "Você já está inscrito nesta função.", ephemeral=True
            )
            return
        if current_count >= limit:
            await interaction.response.send_message(
                f"Não há vagas disponíveis para **{role}**.",
                ephemeral=True,
            )
            return

        await upsert_participant(pool, session_id, interaction.user.id, role)

        lfg_role_id = await get_setting(
            pool, interaction.guild_id, "lfg_notify_role_id"
        )
        data = await get_session_by_id(pool, session_id)
        participants = data["participants"]
        session = _inject_lfg_role(
            data["session"], int(lfg_role_id) if lfg_role_id else None
        )
        embed = await build_lfg_embed(
            session, participants, data["pending_claims"], interaction.guild
        )
        view = LFGSessionView(session_id, slots_config, participants)
        try:
            await interaction.response.edit_message(embed=embed, view=view)
        except discord.NotFound:
            pass


async def _join_queue(
    pool: asyncpg.Pool,
    interaction: discord.Interaction,
    session_id: int,
) -> None:
    from bot.core.guild_settings import get_guild_config, get_setting
    from bot.core.locks import get_lock
    from bot.core.permissions import has_member_role
    from bot.services.lfg_repository import (
        get_session_by_id,
        queue_participant,
    )
    from bot.cogs.lfg.lfg_embed import build_lfg_embed
    from bot.cogs.lfg.lfg_views import LFGSessionView

    config = await get_guild_config(pool, interaction.guild_id)
    if not has_member_role(config, interaction.user):
        await interaction.response.send_message(
            "Você precisa ter o cargo de membro da guilda.",
            ephemeral=True,
        )
        return

    async with get_lock(interaction.guild_id, session_id):
        data = await get_session_by_id(pool, session_id)
        if data is None or data["session"]["status"] != "active":
            await interaction.response.send_message(
                "Esta sessão não está mais ativa.", ephemeral=True
            )
            return

        already_in = next(
            (
                p
                for p in data["participants"]
                if p["user_id"] == interaction.user.id
            ),
            None,
        )
        if already_in and already_in.get("role") is not None:
            await interaction.response.send_message(
                "Você já está inscrito em uma função. Saia primeiro para entrar na fila.",
                ephemeral=True,
            )
            return
        if already_in and already_in.get("role") is None:
            await interaction.response.send_message(
                "Você já está na fila.", ephemeral=True
            )
            return

        await queue_participant(pool, session_id, interaction.user.id)

        lfg_role_id = await get_setting(
            pool, interaction.guild_id, "lfg_notify_role_id"
        )
        data = await get_session_by_id(pool, session_id)
        slots_config = data["session"].get("slots_config") or {}
        participants = data["participants"]
        session = _inject_lfg_role(
            data["session"], int(lfg_role_id) if lfg_role_id else None
        )
        embed = await build_lfg_embed(
            session, participants, data["pending_claims"], interaction.guild
        )
        view = LFGSessionView(session_id, slots_config, participants)
        try:
            await interaction.response.edit_message(embed=embed, view=view)
        except discord.NotFound:
            pass


async def _leave_session(
    pool: asyncpg.Pool,
    interaction: discord.Interaction,
    session_id: int,
) -> None:
    from bot.services.lfg_repository import (
        get_session_by_id,
        remove_participant,
    )
    from bot.core.locks import get_lock
    from bot.core.guild_settings import get_setting
    from bot.cogs.lfg.lfg_embed import build_lfg_embed
    from bot.cogs.lfg.lfg_views import LFGSessionView

    async with get_lock(interaction.guild_id, session_id):
        removed_role = await remove_participant(
            pool, session_id, interaction.user.id
        )
        if removed_role is None:
            await interaction.response.send_message(
                "Você não está participando desta sessão.", ephemeral=True
            )
            return

        data = await get_session_by_id(pool, session_id)
        if data is None:
            await interaction.response.send_message(
                "Sessão não encontrada.", ephemeral=True
            )
            return

        lfg_role_id = await get_setting(
            pool, interaction.guild_id, "lfg_notify_role_id"
        )
        slots_config = data["session"].get("slots_config") or {}
        participants = data["participants"]
        session = _inject_lfg_role(
            data["session"], int(lfg_role_id) if lfg_role_id else None
        )
        embed = await build_lfg_embed(
            session, participants, data["pending_claims"], interaction.guild
        )
        view = LFGSessionView(session_id, slots_config, participants)
        try:
            await interaction.response.edit_message(embed=embed, view=view)
        except discord.NotFound:
            pass


async def _close_session(
    pool: asyncpg.Pool,
    interaction: discord.Interaction,
    session_id: int,
) -> None:
    from bot.services.lfg_repository import (
        get_session_by_id,
        update_session_status,
    )
    from bot.core.locks import get_lock
    from bot.cogs.lfg.lfg_embed import build_lfg_embed
    from bot.cogs.lfg.lfg_views import LFGSessionView

    data = await get_session_by_id(pool, session_id)
    if data is None:
        await interaction.response.send_message(
            "Sessão não encontrada.", ephemeral=True
        )
        return

    session = data["session"]
    is_creator = session["creator_id"] == interaction.user.id
    perms = getattr(interaction.user, "guild_permissions", None)
    is_staff = perms is not None and perms.manage_guild
    if not (is_creator or is_staff):
        await interaction.response.send_message(
            "Apenas o criador ou staff pode encerrar esta sessão.",
            ephemeral=True,
        )
        return

    async with get_lock(interaction.guild_id, session_id):
        data = await get_session_by_id(pool, session_id)
        if data is None or data["session"]["status"] != "active":
            await interaction.response.send_message(
                "Esta sessão já foi encerrada.", ephemeral=True
            )
            return
        await update_session_status(pool, session_id, "closed")

    data = await get_session_by_id(pool, session_id)
    embed = await build_lfg_embed(
        data["session"], data["participants"], data["pending_claims"],
        interaction.guild,
    )
    slots_config = data["session"].get("slots_config") or {}
    view = LFGSessionView(
        session_id, slots_config, data["participants"]
    )
    for item in view.children:
        item.disabled = True
    try:
        await interaction.response.edit_message(embed=embed, view=view)
    except discord.NotFound:
        pass
