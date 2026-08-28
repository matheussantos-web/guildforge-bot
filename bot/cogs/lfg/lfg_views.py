from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

import asyncpg
import discord

from bot.core.branding import get_role_emoji

log = logging.getLogger(__name__)

MAX_QUEUE_SIZE = 25


def _find_role_cfg(
    slots_config: list[dict[str, Any]], role_name: str
) -> dict[str, Any] | None:
    for entry in slots_config:
        if entry.get("role") == role_name:
            return entry
    return None


def _build_select_options(
    slots_config: list[dict[str, Any]],
    participants: list[dict],
) -> list[discord.SelectOption]:
    counts: dict[str, int] = defaultdict(int)
    for p in participants:
        role = p.get("role")
        if role:
            counts[role] += 1

    options: list[discord.SelectOption] = []
    for entry in slots_config:
        role_name = entry.get("role", "")
        limit = entry.get("limit", 1)
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
        slots_config: list[dict[str, Any]] | None = None,
        participants: list[dict] | None = None,
    ) -> None:
        super().__init__(timeout=None)
        self.session_id = session_id
        cfg = slots_config or []
        parts = participants or []

        options = _build_select_options(cfg, parts)
        self.add_item(LFGRoleSelect(session_id, options))

        self.add_item(
            LFGSessionButton(
                session_id, "leave", "Sair",
                discord.ButtonStyle.secondary, row=1,
            )
        )
        self.add_item(
            LFGSessionButton(
                session_id, "close", "Encerrar",
                discord.ButtonStyle.secondary, row=1,
            )
        )
        self.add_item(
            LFGSessionButton(
                session_id, "edit", "✏️ Editar",
                discord.ButtonStyle.secondary, row=2,
            )
        )
        self.add_item(
            LFGSessionButton(
                session_id, "cancel", "🗑️ Cancelar",
                discord.ButtonStyle.danger, row=2,
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
        row: int | None = None,
    ) -> None:
        kwargs: dict[str, Any] = dict(
            custom_id=f"lfg_{action}:{session_id}",
            label=label,
            style=style,
        )
        if row is not None:
            kwargs["row"] = row
        super().__init__(**kwargs)
        self.session_id = session_id
        self.action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        await _handle_lfg_session_interaction(
            interaction, self.session_id, self.action
        )


class EditContentModal(discord.ui.Modal, title="Editar evento de LFG"):
    title_input = discord.ui.TextInput(
        label="Título",
        max_length=100,
    )
    description_input = discord.ui.TextInput(
        label="Descrição (opcional)",
        required=False,
        max_length=500,
    )
    event_time_input = discord.ui.TextInput(
        label="Horário (opcional)",
        placeholder="Ex: 20:00 BRT",
        max_length=50,
        required=False,
    )
    slots_config_input = discord.ui.TextInput(
        label="Composição de Vagas (deixe vazio para manter)",
        placeholder="Ex: Tank:1:Front, Healer:1:Front, DPS:5:DPS",
        style=discord.TextStyle.paragraph,
        max_length=500,
        required=False,
    )

    def __init__(
        self,
        session_id: int,
        current_title: str,
        current_description: str,
        current_event_time: str,
    ) -> None:
        super().__init__()
        self.session_id = session_id
        self.title_input.default = current_title
        self.description_input.default = current_description
        self.event_time_input.default = current_event_time

    async def on_submit(self, interaction: discord.Interaction) -> None:
        pool: asyncpg.Pool = interaction.client.db_pool
        from bot.core.locks import get_lock
        from bot.services.lfg_repository import (
            get_session_by_id,
            update_session_meta,
            update_session_slots,
        )

        new_slots_raw = self.slots_config_input.value.strip() if self.slots_config_input.value else ""

        async with get_lock(interaction.guild_id, self.session_id):
            data = await get_session_by_id(pool, self.session_id)
            if data is None or data["session"]["status"] != "active":
                await interaction.response.send_message(
                    "Esta sessão não está mais ativa.", ephemeral=True
                )
                return

            await update_session_meta(
                pool,
                self.session_id,
                title=self.title_input.value,
                description=self.description_input.value or "",
                event_time=self.event_time_input.value or "",
            )

            if new_slots_raw:
                parsed = _parse_slots(new_slots_raw)
                if parsed is None:
                    await interaction.response.send_message(
                        "Formato de vagas inválido. Use **Nome:Vagas:Categoria** separados por vírgula.",
                        ephemeral=True,
                    )
                    return
                old_roles = {
                    e.get("role") for e in (data["session"].get("slots_config") or [])
                }
                new_roles = {e.get("role") for e in parsed}
                removed = [r for r in old_roles if r not in new_roles]
                await update_session_slots(pool, self.session_id, parsed, removed)

        result = await _rebuild_session_view(
            pool, interaction.guild, self.session_id
        )
        if result is None:
            await interaction.response.send_message(
                "Sessão não encontrada.", ephemeral=True
            )
            return
        embed, content, view, _ = result
        try:
            await interaction.response.edit_message(
                content=content, embed=embed, view=view
            )
        except discord.NotFound:
            pass


class CancelConfirmView(discord.ui.View):
    def __init__(self, session_id: int, original_message_id: int) -> None:
        super().__init__(timeout=60)
        self.session_id = session_id
        self.original_message_id = original_message_id
        self._timed_out = False

    async def on_timeout(self) -> None:
        self._timed_out = True
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(
                    content="⏱️ Tempo esgotado. Cancelamento não realizado.",
                    view=self,
                )
            except (discord.NotFound, discord.HTTPException):
                pass

    @discord.ui.button(
        label="Confirmar cancelamento",
        style=discord.ButtonStyle.danger,
    )
    async def confirm(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if self._timed_out:
            await interaction.response.send_message(
                "Esta confirmação expirou. Use 🗑️ Cancelar novamente.",
                ephemeral=True,
            )
            return
        pool: asyncpg.Pool = interaction.client.db_pool
        from bot.core.locks import get_lock
        from bot.services.lfg_repository import (
            get_session_by_id,
            update_session_status,
        )

        async with get_lock(interaction.guild_id, self.session_id):
            data = await get_session_by_id(pool, self.session_id)
            if data is None or data["session"]["status"] != "active":
                await interaction.response.edit_message(
                    content="Esta sessão já foi encerrada.", view=None
                )
                return

            await update_session_status(
                pool, self.session_id, "cancelled"
            )

        from bot.core.locks import release_lock
        release_lock(interaction.guild_id, self.session_id)

        result = await _rebuild_session_view(
            pool, interaction.guild, self.session_id
        )
        if result is None:
            await interaction.response.edit_message(
                content="Sessão não encontrada.", view=None
            )
            return
        embed, content, view, data = result
        for item in view.children:
            item.disabled = True

        channel = interaction.guild.get_channel(
            data["session"]["channel_id"]
        )
        if channel:
            try:
                original_msg = await channel.fetch_message(
                    self.original_message_id
                )
                await original_msg.edit(content=content, embed=embed, view=view)
            except (discord.NotFound, discord.Forbidden):
                pass

            title = data["session"].get("title", "LFG")
            participant_mentions = [
                f"<@{p['user_id']}>"
                for p in data["participants"]
                if p.get("role") is not None
            ]
            if participant_mentions:
                try:
                    await channel.send(
                        f"⚠️ O evento **{title}** foi cancelado pelo "
                        f"organizador.\n"
                        f"Afetados: {', '.join(participant_mentions)}"
                    )
                except discord.Forbidden:
                    pass

        await interaction.response.edit_message(
            content="✅ Evento cancelado com sucesso.", view=None
        )

    @discord.ui.button(
        label="Voltar",
        style=discord.ButtonStyle.secondary,
    )
    async def back(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if self._timed_out:
            await interaction.response.send_message(
                "Esta confirmação expirou. Use 🗑️ Cancelar novamente.",
                ephemeral=True,
            )
            return
        await interaction.response.edit_message(
            content="Cancelamento descartado.", view=None
        )


def _inject_lfg_role(session: dict, lfg_role_id: int | None) -> dict:
    if lfg_role_id:
        session = dict(session)
        session["lfg_role_id"] = lfg_role_id
    return session


async def _fetch_lfg_role_id(pool: asyncpg.Pool, guild_id: int) -> int | None:
    from bot.core.guild_settings import get_setting

    val = await get_setting(pool, guild_id, "lfg_notify_role_id")
    return int(val) if val else None


async def _fetch_guild_timezone(pool: asyncpg.Pool, guild_id: int) -> str | None:
    from bot.core.guild_settings import get_setting

    return await get_setting(pool, guild_id, "guild_timezone")


async def _fetch_guild_display_name(pool: asyncpg.Pool, guild_id: int) -> str | None:
    from bot.core.guild_settings import get_setting
    return await get_setting(pool, guild_id, "guild_display_name")


async def _rebuild_session_view(
    pool: asyncpg.Pool,
    guild: discord.Guild,
    session_id: int,
) -> tuple[discord.Embed, str, LFGSessionView, dict] | None:
    from bot.cogs.lfg.lfg_embed import build_lfg_embed
    from bot.services.lfg_repository import get_session_by_id

    data = await get_session_by_id(pool, session_id)
    if data is None:
        return None
    lfg_role_id = await _fetch_lfg_role_id(pool, guild.id)
    session = _inject_lfg_role(data["session"], lfg_role_id)
    tz_name = await _fetch_guild_timezone(pool, guild.id)
    guild_display_name = await _fetch_guild_display_name(pool, guild.id)
    embed, content = await build_lfg_embed(
        session, data["participants"], data["pending_claims"], guild,
        tz_name, guild_display_name,
    )
    slots_config = data["session"].get("slots_config") or []
    view = LFGSessionView(session_id, slots_config, data["participants"])
    return embed, content, view, data


def _check_event_admin(session: dict, user: discord.Member) -> bool:
    is_creator = session["creator_id"] == user.id
    perms = getattr(user, "guild_permissions", None)
    is_staff = perms is not None and perms.manage_guild
    return is_creator or is_staff


async def _handle_lfg_session_interaction(
    interaction: discord.Interaction,
    session_id: int,
    action: str,
    role_value: str | None = None,
) -> None:
    if interaction.response.is_done():
        return

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
            await _handle_close_request(interaction, session_id)
        elif action == "edit":
            await _handle_edit_session(interaction, session_id)
        elif action == "cancel":
            await _handle_cancel_request(interaction, session_id)
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


async def _handle_edit_session(
    interaction: discord.Interaction,
    session_id: int,
) -> None:
    pool: asyncpg.Pool = interaction.client.db_pool
    from bot.services.lfg_repository import get_session_by_id

    data = await get_session_by_id(pool, session_id)
    if data is None:
        await interaction.response.send_message(
            "Sessão não encontrada.", ephemeral=True
        )
        return

    if not _check_event_admin(data["session"], interaction.user):
        await interaction.response.send_message(
            "Só o criador do evento ou um admin pode editar.",
            ephemeral=True,
        )
        return

    session = data["session"]
    modal = EditContentModal(
        session_id,
        current_title=session.get("title", ""),
        current_description=session.get("description", ""),
        current_event_time=session.get("event_time", ""),
    )
    await interaction.response.send_modal(modal)


class ConfirmCloseView(discord.ui.View):
    def __init__(self, session_id: int, original_message_id: int) -> None:
        super().__init__(timeout=60)
        self.session_id = session_id
        self.original_message_id = original_message_id
        self._timed_out = False

    async def on_timeout(self) -> None:
        self._timed_out = True
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(
                    content="⏱️ Tempo esgotado. Encerramento não realizado.",
                    view=self,
                )
            except (discord.NotFound, discord.HTTPException):
                pass

    @discord.ui.button(
        label="Confirmar encerramento",
        style=discord.ButtonStyle.secondary,
    )
    async def confirm(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if self._timed_out:
            await interaction.response.send_message(
                "Esta confirmação expirou. Use Encerrar novamente.",
                ephemeral=True,
            )
            return
        await interaction.response.edit_message(
            content="⏳ Encerrando sessão...", view=None
        )
        await _close_session(
            interaction.client.db_pool,
            interaction,
            self.session_id,
            target_message_id=self.original_message_id,
        )

    @discord.ui.button(
        label="Voltar",
        style=discord.ButtonStyle.secondary,
    )
    async def back(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if self._timed_out:
            await interaction.response.send_message(
                "Esta confirmação expirou. Use Encerrar novamente.",
                ephemeral=True,
            )
            return
        await interaction.response.edit_message(
            content="Encerramento descartado.", view=None
        )


async def _handle_close_request(
    interaction: discord.Interaction,
    session_id: int,
) -> None:
    pool: asyncpg.Pool = interaction.client.db_pool
    from bot.services.lfg_repository import get_session_by_id

    data = await get_session_by_id(pool, session_id)
    if data is None:
        await interaction.response.send_message(
            "Sessão não encontrada.", ephemeral=True
        )
        return

    if not _check_event_admin(data["session"], interaction.user):
        await interaction.response.send_message(
            "Apenas o criador ou staff pode encerrar esta sessão.",
            ephemeral=True,
        )
        return

    title = data["session"].get("title", "LFG")
    original_message_id = data["session"].get("message_id")
    if not original_message_id:
        await interaction.response.send_message(
            "Sessão sem mensagem vinculada. Não é possível encerrar.",
            ephemeral=True,
        )
        return
    view = ConfirmCloseView(session_id, original_message_id)
    await interaction.response.send_message(
        f"⚠️ Tem certeza que deseja encerrar o evento **{title}**?\n"
        f"Todos os participantes serão notificados e os botões serão desabilitados.",
        view=view,
        ephemeral=True,
    )
    view.message = await interaction.original_response()


async def _handle_cancel_request(
    interaction: discord.Interaction,
    session_id: int,
) -> None:
    pool: asyncpg.Pool = interaction.client.db_pool
    from bot.services.lfg_repository import get_session_by_id

    data = await get_session_by_id(pool, session_id)
    if data is None:
        await interaction.response.send_message(
            "Sessão não encontrada.", ephemeral=True
        )
        return

    if not _check_event_admin(data["session"], interaction.user):
        await interaction.response.send_message(
            "Só o criador do evento ou um admin pode cancelar.",
            ephemeral=True,
        )
        return

    title = data["session"].get("title", "LFG")
    view = CancelConfirmView(session_id, interaction.message.id)
    await interaction.response.send_message(
        f"⚠️ Tem certeza que deseja cancelar o evento **{title}**?\n"
        f"Isso irá desabilitar todos os botões e notificar os participantes.",
        view=view,
        ephemeral=True,
    )
    view.message = await interaction.original_response()


async def _join_with_role(
    pool: asyncpg.Pool,
    interaction: discord.Interaction,
    session_id: int,
    role: str,
) -> None:
    from bot.core.guild_settings import get_guild_config
    from bot.core.locks import get_lock
    from bot.core.permissions import has_member_role
    from bot.services.lfg_repository import (
        get_session_by_id,
        upsert_participant,
    )

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

        slots_config = data["session"].get("slots_config") or []
        role_cfg = _find_role_cfg(slots_config, role)
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

    result = await _rebuild_session_view(
        pool, interaction.guild, session_id
    )
    if result is None:
        return
    embed, content, view, _ = result
    try:
        await interaction.response.edit_message(
            content=content, embed=embed, view=view
        )
    except discord.NotFound:
        pass


async def _join_queue(
    pool: asyncpg.Pool,
    interaction: discord.Interaction,
    session_id: int,
) -> None:
    from bot.core.guild_settings import get_guild_config
    from bot.core.locks import get_lock
    from bot.core.permissions import has_member_role
    from bot.services.lfg_repository import (
        get_session_by_id,
        queue_participant,
    )

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
                "Você já está inscrito em uma função. "
                "Saia primeiro para entrar na fila.",
                ephemeral=True,
            )
            return
        elif already_in and already_in.get("role") is None:
            await interaction.response.send_message(
                "Você já está na fila.", ephemeral=True
            )
            return

        current_queue = sum(
            1 for p in data["participants"] if p.get("role") is None
        )
        if current_queue >= MAX_QUEUE_SIZE:
            await interaction.response.send_message(
                f"A fila está lotada ({MAX_QUEUE_SIZE} participantes máximos). "
                "Tente novamente mais tarde.",
                ephemeral=True,
            )
            return

        await queue_participant(pool, session_id, interaction.user.id)

    result = await _rebuild_session_view(
        pool, interaction.guild, session_id
    )
    if result is None:
        return
    embed, content, view, _ = result
    try:
        await interaction.response.edit_message(
            content=content, embed=embed, view=view
        )
    except discord.NotFound:
        pass


async def _leave_session(
    pool: asyncpg.Pool,
    interaction: discord.Interaction,
    session_id: int,
) -> None:
    from bot.core.locks import get_lock
    from bot.services.lfg_repository import (
        get_session_by_id,
        remove_participant,
    )

    async with get_lock(interaction.guild_id, session_id):
        removed_role = await remove_participant(
            pool, session_id, interaction.user.id
        )
        if removed_role is None:
            await interaction.response.send_message(
                "Você não está participando desta sessão.",
                ephemeral=True,
            )
            return

        data = await get_session_by_id(pool, session_id)
        if data is None:
            await interaction.response.send_message(
                "Sessão não encontrada.", ephemeral=True
            )
            return

    result = await _rebuild_session_view(
        pool, interaction.guild, session_id
    )
    if result is None:
        return
    embed, content, view, _ = result
    try:
        await interaction.response.edit_message(
            content=content, embed=embed, view=view
        )
    except discord.NotFound:
        pass


async def _close_session(
    pool: asyncpg.Pool,
    interaction: discord.Interaction,
    session_id: int,
    target_message_id: int | None = None,
) -> None:
    from bot.core.locks import get_lock
    from bot.services.lfg_repository import (
        get_session_by_id,
        update_session_status,
    )

    async def send_error(msg: str) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

    data = await get_session_by_id(pool, session_id)
    if data is None:
        await send_error("Sessão não encontrada.")
        return

    if not _check_event_admin(data["session"], interaction.user):
        await send_error(
            "Apenas o criador ou staff pode encerrar esta sessão."
        )
        return

    async with get_lock(interaction.guild_id, session_id):
        data = await get_session_by_id(pool, session_id)
        if data is None or data["session"]["status"] != "active":
            await send_error("Esta sessão já foi encerrada.")
            return
        await update_session_status(pool, session_id, "closed")

    from bot.core.locks import release_lock
    release_lock(interaction.guild_id, session_id)

    result = await _rebuild_session_view(
        pool, interaction.guild, session_id
    )
    if result is None:
        return
    embed, content, view, _ = result
    for item in view.children:
        item.disabled = True

    if target_message_id is not None:
        channel = interaction.guild.get_channel(data["session"]["channel_id"])
        try:
            original_msg = await channel.fetch_message(target_message_id)
            await original_msg.edit(content=content, embed=embed, view=view)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
    else:
        try:
            await interaction.response.edit_message(
                content=content, embed=embed, view=view
            )
        except discord.NotFound:
            pass
