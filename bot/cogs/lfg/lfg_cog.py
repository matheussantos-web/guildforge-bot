import logging
from typing import Any

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands

from bot.core.guild_settings import get_guild_config
from bot.core.locks import get_lock

log = logging.getLogger(__name__)

EVENT_INSERT = """
    INSERT INTO content_events (guild_id, created_by_member_id, type, status, max_participants)
    VALUES ($1, $2, $3, 'open', $4)
    RETURNING id
"""

EVENT_FETCH = """
    SELECT e.id, e.guild_id, e.type, e.status, e.closed_at, e.created_at, e.max_participants,
           m.discord_user_id AS creator_discord_id
    FROM content_events e
    JOIN members m ON m.id = e.created_by_member_id
    WHERE e.id = $1
"""

PARTICIPANTS_FETCH = """
    SELECT m.discord_user_id, m.albion_character_name
    FROM content_participants cp
    JOIN members m ON m.id = cp.member_id
    WHERE cp.event_id = $1
    ORDER BY cp.joined_at
"""

CREATOR_FETCH = """
    SELECT m.discord_user_id
    FROM content_events e
    JOIN members m ON m.id = e.created_by_member_id
    WHERE e.id = $1
"""

JOIN_SQL = """
    INSERT INTO content_participants (event_id, member_id)
    VALUES ($1, $2)
    ON CONFLICT DO NOTHING
"""


def _build_embed(event, participants: list[asyncpg.Record]) -> discord.Embed:
    status = event["status"]
    color = discord.Color.green() if status == "open" else discord.Color.red()
    embed = discord.Embed(title=f"Evento: {event['type']}", color=color)
    if status != "open":
        embed.description = "Este evento foi **encerrado**."
    embed.add_field(
        name="Criado por",
        value=f"<@{event['creator_discord_id']}>",
        inline=True,
    )
    embed.add_field(
        name="Vagas",
        value=f"{len(participants)}/{event['max_participants']}",
        inline=True,
    )
    lines = []
    for participant in participants[:20]:
        name = participant["albion_character_name"]
        line = f"<@{participant['discord_user_id']}>"
        if name:
            line += f" — {name}"
        lines.append(line)
    extra = len(participants) - 20
    if extra > 0:
        lines.append(f"+{extra} participante(s)")
    embed.add_field(
        name="Participantes",
        value="\n".join(lines) if lines else "Nenhum participante ainda.",
        inline=False,
    )
    return embed


async def _member_id(pool: asyncpg.Pool, guild_id: int, discord_user_id: int) -> int | None:
    return await pool.fetchval(
        "SELECT id FROM members WHERE guild_id = $1 AND discord_user_id = $2",
        guild_id,
        discord_user_id,
    )


async def _refresh_message(
    pool: asyncpg.Pool,
    interaction: discord.Interaction,
    event_id: int,
) -> None:
    event = await pool.fetchrow(EVENT_FETCH, event_id)
    if event is None:
        return
    participants = await pool.fetch(PARTICIPANTS_FETCH, event_id)
    embed = _build_embed(event, participants)
    try:
        await interaction.response.edit_message(
            embed=embed,
            view=ContentEventView(event_id),
        )
    except discord.NotFound:
        pass


async def _join_event(
    pool: asyncpg.Pool,
    interaction: discord.Interaction,
    event_id: int,
) -> None:
    member = await _member_id(pool, interaction.guild_id, interaction.user.id)
    if member is None:
        await interaction.response.send_message(
            "Você precisa se registrar com `/registrar` antes de entrar em eventos.",
            ephemeral=True,
        )
        return

    async with pool.acquire() as conn:
        async with conn.transaction():
            event = await conn.fetchrow(
                "SELECT status, max_participants FROM content_events WHERE id = $1 FOR UPDATE",
                event_id,
            )
            if event is None:
                await interaction.response.send_message(
                    "Evento não encontrado.",
                    ephemeral=True,
                )
                return
            if event["status"] != "open":
                await interaction.response.send_message(
                    "Este evento já foi encerrado.",
                    ephemeral=True,
                )
                return
            count = await conn.fetchval(
                "SELECT count(*) FROM content_participants WHERE event_id = $1",
                event_id,
            )
            if count >= event["max_participants"]:
                await interaction.response.send_message(
                    "As vagas deste evento já estão preenchidas.",
                    ephemeral=True,
                )
                return
            await conn.execute(JOIN_SQL, event_id, member)

    await _refresh_message(pool, interaction, event_id)


async def _leave_event(
    pool: asyncpg.Pool,
    interaction: discord.Interaction,
    event_id: int,
) -> None:
    member = await _member_id(pool, interaction.guild_id, interaction.user.id)
    if member is None:
        await interaction.response.send_message(
            "Você ainda não está registrado para participar de eventos.",
            ephemeral=True,
        )
        return

    async with pool.acquire() as conn:
        async with conn.transaction():
            event = await conn.fetchrow(
                "SELECT status FROM content_events WHERE id = $1 FOR UPDATE",
                event_id,
            )
            if event is None:
                await interaction.response.send_message(
                    "Evento não encontrado.",
                    ephemeral=True,
                )
                return
            await conn.execute(
                "DELETE FROM content_participants WHERE event_id = $1 AND member_id = $2",
                event_id,
                member,
            )

    await _refresh_message(pool, interaction, event_id)


async def _close_event(
    pool: asyncpg.Pool,
    interaction: discord.Interaction,
    event_id: int,
) -> None:
    creator = await pool.fetchrow(CREATOR_FETCH, event_id)
    if creator is None:
        await interaction.response.send_message(
            "Evento não encontrado.",
            ephemeral=True,
        )
        return

    permissions = getattr(interaction.user, "guild_permissions", None)
    is_creator = creator["discord_user_id"] == interaction.user.id
    is_staff = permissions is not None and permissions.manage_guild
    if not (is_creator or is_staff):
        await interaction.response.send_message(
            "Apenas o criador do evento ou staff pode encerrá-lo.",
            ephemeral=True,
        )
        return

    async with get_lock(interaction.guild_id, event_id):
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT status, guild_id FROM content_events WHERE id = $1 FOR UPDATE",
                    event_id,
                )
                if row is None:
                    await interaction.response.send_message(
                        "Evento não encontrado.",
                        ephemeral=True,
                    )
                    return
                if row["status"] == "closed":
                    await interaction.response.send_message(
                        "Este evento já foi encerrado.",
                        ephemeral=True,
                    )
                    return
                await conn.execute(
                    "UPDATE content_events SET status = 'closed', closed_at = now() WHERE id = $1",
                    event_id,
                )

    event = await pool.fetchrow(EVENT_FETCH, event_id)
    participants = await pool.fetch(PARTICIPANTS_FETCH, event_id)
    embed = _build_embed(event, participants)

    view = ContentEventView(event_id)
    for item in view.children:
        item.disabled = True
    try:
        await interaction.response.edit_message(embed=embed, view=view)
    except discord.NotFound:
        pass

    await _send_close_summary(pool, interaction, event_id, event, participants)


async def _send_close_summary(
    pool: asyncpg.Pool,
    interaction: discord.Interaction,
    event_id: int,
    event,
    participants: list[asyncpg.Record],
) -> None:
    config = await get_guild_config(pool, event["guild_id"])
    log_channel_id = config.get("log_channel_id") if config else None
    if not log_channel_id:
        return

    channel = interaction.guild.get_channel(log_channel_id)
    if channel is None or not isinstance(channel, discord.TextChannel):
        return

    summary = discord.Embed(title="Evento encerrado", color=discord.Color.red())
    summary.add_field(name="Tipo", value=event["type"], inline=True)
    summary.add_field(name="Participantes finais", value=str(len(participants)), inline=True)
    if event["closed_at"] is not None and event["created_at"] is not None:
        summary.add_field(
            name="Duração",
            value=str(event["closed_at"] - event["created_at"]),
            inline=True,
        )
    if participants:
        summary.add_field(
            name="Quem participou",
            value="\n".join(f"<@{p['discord_user_id']}>" for p in participants),
            inline=False,
        )
    try:
        await channel.send(embed=summary)
    except (discord.Forbidden, discord.HTTPException):
        log.warning(
            "Não foi possível enviar resumo do evento %s no canal %s",
            event_id,
            log_channel_id,
        )


async def _handle_lfg_interaction(
    interaction: discord.Interaction,
    event_id: int,
    action: str,
) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            "Esta interação só funciona em um servidor.",
            ephemeral=True,
        )
        return

    pool: asyncpg.Pool = interaction.client.db_pool
    try:
        if action == "join":
            await _join_event(pool, interaction, event_id)
        elif action == "leave":
            await _leave_event(pool, interaction, event_id)
        elif action == "close":
            await _close_event(pool, interaction, event_id)
    except Exception:
        log.exception("Erro ao processar interação LFG '%s' do evento %s", action, event_id)
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


class LFGButton(discord.ui.Button):
    def __init__(
        self,
        event_id: int,
        action: str,
        label: str,
        style: discord.ButtonStyle,
    ) -> None:
        super().__init__(
            custom_id=f"lfg_{action}:{event_id}",
            label=label,
            style=style,
        )
        self.event_id = event_id
        self.action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        await _handle_lfg_interaction(interaction, self.event_id, self.action)


class ContentEventView(discord.ui.View):
    def __init__(self, event_id: int) -> None:
        super().__init__(timeout=None)
        self.event_id = event_id
        self.add_item(LFGButton(event_id, "join", "Entrar", discord.ButtonStyle.success))
        self.add_item(LFGButton(event_id, "leave", "Sair", discord.ButtonStyle.secondary))
        self.add_item(LFGButton(event_id, "close", "Encerrar", discord.ButtonStyle.danger))


class LFGCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def pool(self) -> Any:
        return self.bot.db_pool

    @app_commands.command(
        name="content",
        description="Cria um evento de LFG com vagas para membros entrarem",
    )
    @app_commands.describe(
        type="Tipo do evento (ex: Dungeon T8, Roaming, Ganking)",
        max_participants="Número máximo de participantes",
    )
    async def content(
        self,
        interaction: discord.Interaction,
        type: str,
        max_participants: app_commands.Range[int, 1, 99] = 10,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use este comando em um servidor.",
                ephemeral=True,
            )
            return

        config = await get_guild_config(self.pool, interaction.guild.id)
        if config is None:
            await interaction.response.send_message(
                "Este servidor ainda não foi configurado. Peça a um administrador "
                "para rodar `/setup` antes de criar eventos.",
                ephemeral=True,
            )
            return

        member = await _member_id(self.pool, interaction.guild.id, interaction.user.id)
        if member is None:
            await interaction.response.send_message(
                "Você precisa se registrar com `/registrar` antes de criar eventos.",
                ephemeral=True,
            )
            return

        async with self.pool.acquire() as conn:
            event_id = await conn.fetchval(
                EVENT_INSERT,
                interaction.guild.id,
                member,
                type,
                max_participants,
            )

        event = await self.pool.fetchrow(EVENT_FETCH, event_id)
        embed = _build_embed(event, [])
        await interaction.response.send_message(
            embed=embed,
            view=ContentEventView(event_id),
        )

    async def _register_open_views(self) -> None:
        if self.pool is None:
            return
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id FROM content_events WHERE status = 'open'",
            )
        for row in rows:
            self.bot.add_view(ContentEventView(row["id"]))
        if rows:
            log.info(
                "Registradas %d view(s) persistente(s) de eventos abertos",
                len(rows),
            )


async def setup(bot: commands.Bot) -> None:
    cog = LFGCog(bot)
    await bot.add_cog(cog)
    await cog._register_open_views()
