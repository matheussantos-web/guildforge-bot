"""Camada de apresentação das views de LFG (menus, botões e modais).

Este módulo contém apenas UI do Discord. Toda a lógica de negócio (persistência,
regras de vagas/fila, encerramento) está em ``bot.services.lfg_service``, que
retorna ``(ok, mensagem)`` tipados e traduzidos aqui em interações com o usuário.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

import asyncpg
import discord

from bot.core.branding import get_role_emoji
from bot.core.db import get_pool
from bot.services.lfg_service import LFGService, parse_slots

log = logging.getLogger(__name__)


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
        if isinstance(entry, dict):
            role_name = entry.get("role", "")
            limit = entry.get("limit", 1)
        else:
            role_name = str(entry)
            limit = 1
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


async def _refresh_message(
    interaction: discord.Interaction,
    session_id: int,
) -> None:
    """Reconstrói e atualiza a mensagem LFG com o estado atual da sessão."""
    service = LFGService(get_pool())
    try:
        result = await service.rebuild_view(interaction.guild, session_id)
    except Exception:
        log.exception("Falha ao reconstruir view da sessão %s", session_id)
        await _reply_or_followup(interaction, "Algo deu errado ao atualizar o evento.")
        return
    if result is None:
        await _reply_or_followup(interaction, "Sessão não encontrada.")
        return
    embed, content, view, _ = result
    try:
        await interaction.response.edit_message(
            content=content, embed=embed, view=view
        )
    except discord.NotFound:
        pass


async def _reply_or_followup(interaction: discord.Interaction, message: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


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
        pool: asyncpg.Pool = get_pool()
        from bot.core.locks import lock_for
        from bot.services.lfg_repository import (
            get_session_by_id,
            update_session_meta,
            update_session_slots,
        )

        new_slots_raw = self.slots_config_input.value.strip() if self.slots_config_input.value else ""

        async with lock_for(interaction.guild_id, self.session_id):
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
                parsed = parse_slots(new_slots_raw)
                if parsed is None:
                    await interaction.response.send_message(
                        "Formato de vagas inválido. Use **Nome:Vagas:Categoria** separados por vírgula.",
                        ephemeral=True,
                    )
                    return
                old_roles = {
                    entry.get("role") if isinstance(entry, dict) else str(entry)
                    for entry in (data["session"].get("slots_config") or [])
                }
                new_roles = {e.get("role") for e in parsed}
                removed = [r for r in old_roles if r not in new_roles]
                await update_session_slots(pool, self.session_id, parsed, removed)

        await _refresh_message(interaction, self.session_id)


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
        pool: asyncpg.Pool = get_pool()
        from bot.core.locks import lock_for
        from bot.services.lfg_repository import (
            get_session_by_id,
            update_session_status,
        )

        async with lock_for(interaction.guild_id, self.session_id):
            data = await get_session_by_id(pool, self.session_id)
            if data is None or data["session"]["status"] != "active":
                await interaction.response.edit_message(
                    content="Esta sessão já foi encerrada.", view=None
                )
                return

            await update_session_status(
                pool, self.session_id, "cancelled"
            )

        result = await LFGService(pool).rebuild_view(interaction.guild, self.session_id)
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
        await _handle_close_session(interaction, self.session_id, self.original_message_id)

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

    service = LFGService(get_pool())

    try:
        ok, message = await _run_action(service, interaction, session_id, action, role_value)
    except Exception:
        log.exception(
            "Erro ao processar interação LFG '%s' da sessão %s",
            action,
            session_id,
        )
        await _reply_or_followup(interaction, "Algo deu errado ao processar sua interação.")
        return

    if not ok:
        await _reply_or_followup(interaction, message)
        return

    if action in ("join_role", "queue", "leave"):
        await _refresh_message(interaction, session_id)


async def _run_action(
    service: LFGService,
    interaction: discord.Interaction,
    session_id: int,
    action: str,
    role_value: str | None,
) -> tuple[bool, str]:
    guild_id = interaction.guild.id
    user_id = interaction.user.id

    if action == "join_role":
        return await service.join_role(guild_id, user_id, session_id, role_value)
    if action == "queue":
        return await service.join_queue(guild_id, user_id, session_id)
    if action == "leave":
        return await service.leave(guild_id, user_id, session_id)
    if action == "edit":
        await _handle_edit_session(service, interaction, session_id)
        return True, ""
    if action == "close":
        await _handle_close_request(service, interaction, session_id)
        return True, ""
    if action == "cancel":
        await _handle_cancel_request(interaction, session_id)
        return True, ""
    return False, "Ação desconhecida."


async def _handle_close_session(
    interaction: discord.Interaction,
    session_id: int,
    target_message_id: int | None = None,
) -> None:
    service = LFGService(get_pool())
    is_admin = bool(
        getattr(interaction.user, "guild_permissions", None)
        and interaction.user.guild_permissions.manage_guild
    )
    ok, message = await service.close(
        interaction.guild.id, interaction.user.id, session_id, is_admin=is_admin
    )
    if not ok:
        await _reply_or_followup(interaction, message or "Não foi possível encerrar.")
        return

    try:
        result = await service.rebuild_view(interaction.guild, session_id)
    except Exception:
        log.exception("Falha ao reconstruir view ao encerrar sessão %s", session_id)
        return
    if result is None:
        return
    embed, content, view, data = result
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


async def _handle_edit_session(
    service: LFGService,
    interaction: discord.Interaction,
    session_id: int,
) -> None:
    from bot.services.lfg_repository import get_session_by_id

    data = await get_session_by_id(service.pool, session_id)
    if data is None:
        await interaction.response.send_message(
            "Sessão não encontrada.", ephemeral=True
        )
        return

    if not (await _check_event_admin(interaction, data["session"])):
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


async def _handle_close_request(
    service: LFGService,
    interaction: discord.Interaction,
    session_id: int,
) -> None:
    from bot.services.lfg_repository import get_session_by_id

    data = await get_session_by_id(service.pool, session_id)
    if data is None:
        await interaction.response.send_message(
            "Sessão não encontrada.", ephemeral=True
        )
        return

    if not (await _check_event_admin(interaction, data["session"])):
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
    from bot.services.lfg_repository import get_session_by_id

    data = await get_session_by_id(get_pool(), session_id)
    if data is None:
        await interaction.response.send_message(
            "Sessão não encontrada.", ephemeral=True
        )
        return

    if not (await _check_event_admin(interaction, data["session"])):
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


async def _check_event_admin(
    interaction: discord.Interaction, session: dict
) -> bool:
    is_creator = session["creator_id"] == interaction.user.id
    perms = getattr(interaction.user, "guild_permissions", None)
    is_staff = perms is not None and perms.manage_guild
    return is_creator or is_staff
