from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone

import discord

from bot.core.branding import (
    GUILDFORGE_COLOR,
    GUILDFORGE_LOGO_URL,
    get_role_emoji,
)

_MAX_FIELD_CHARS = 1000
_FIELD_LINES_OVERHEAD = 40
_BULLET = "\u2514"


async def _resolve_mentions(
    user_ids: list[int], guild: discord.Guild
) -> list[str]:
    mentions: list[str] = []
    for uid in user_ids:
        member = guild.get_member(uid)
        if member is None:
            try:
                member = await guild.fetch_member(uid)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                member = None
        if member:
            mentions.append(member.mention)
        else:
            mentions.append(f"Indisponivel ({uid})")
    return mentions


def format_role_field(
    role_name: str,
    member_mentions: list[str],
    limit: int,
    max_shown: int = 10,
) -> tuple[str, str]:
    emoji = get_role_emoji(role_name)
    count = len(member_mentions)
    name = f"{emoji} {role_name} `[{count}/{limit}]`"

    if not member_mentions:
        return name, "_Vagas abertas_"

    shown = member_mentions[:max_shown]
    remaining = count - len(shown)

    lines = [f"{_BULLET} {m}" for m in shown]
    if remaining > 0:
        lines.append(f"{_BULLET} *e mais {remaining} jogador(es)...*")

    value = "\n".join(lines)
    while len(value) > _MAX_FIELD_CHARS and len(shown) > 1:
        shown = shown[:-1]
        remaining = count - len(shown)
        lines = [f"{_BULLET} {m}" for m in shown]
        if remaining > 0:
            lines.append(f"{_BULLET} *e mais {remaining} jogador(es)...*")
        value = "\n".join(lines)

    return name, value


def format_queue_field(
    queued_mentions: list[str],
    queued_positions: list[int],
    max_shown: int = 15,
) -> tuple[str, str]:
    count = len(queued_mentions)
    name = f"\u23f3 Fila de Espera `[{count}]`"

    if not queued_mentions:
        return name, "_Ninguem na fila._"

    shown_mentions = queued_mentions[:max_shown]
    shown_positions = queued_positions[:max_shown]
    remaining = count - len(shown_mentions)

    lines = []
    for pos, mention in zip(shown_positions, shown_mentions):
        lines.append(f"`#{pos}` {mention}")
    if remaining > 0:
        lines.append(f"*...e mais {remaining} na fila*")

    value = "\n".join(lines)
    while len(value) > _MAX_FIELD_CHARS and len(shown_mentions) > 1:
        shown_mentions = shown_mentions[:-1]
        shown_positions = shown_positions[:-1]
        remaining = count - len(shown_mentions)
        lines = []
        for pos, mention in zip(shown_positions, shown_mentions):
            lines.append(f"`#{pos}` {mention}")
        if remaining > 0:
            lines.append(f"*...e mais {remaining} na fila*")
        value = "\n".join(lines)

    return name, value


def _parse_event_time(raw: str) -> str | None:
    if not raw:
        return None
    cleaned = raw.strip()
    m = re.match(r"(\d{1,2}):(\d{2})", cleaned)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            now = datetime.now(timezone.utc)
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target <= now:
                from datetime import timedelta
                target += timedelta(days=1)
            return f"<t:{int(target.timestamp())}:R>"
    return None


async def build_lfg_embed(
    session: dict,
    participants: list[dict],
    pending_claims: list[dict],
    guild: discord.Guild,
) -> discord.Embed:
    status = session["status"]
    slots_config = session.get("slots_config") or {}
    creator_id = session["creator_id"]
    title = session.get("title", "LFG")
    description = session.get("description", "")
    event_time = session.get("event_time", "")
    created_at = session.get("created_at")

    color = _resolve_color(status, slots_config, participants)

    embed = discord.Embed(color=color)
    embed.set_author(
        name="GuildForge \u2022 Sistema de LFG",
        icon_url=GUILDFORGE_LOGO_URL,
    )
    embed.title = f"\u2694\ufe0f {title}"

    desc_parts: list[str] = []
    if status == "closed":
        desc_parts.append("**Encerrado**")
    elif status == "cancelled":
        desc_parts.append("**Cancelado**")
    elif _is_group_full(slots_config, participants):
        desc_parts.append("**Grupo completo!**")

    if description:
        desc_parts.append(f"**Descricao:** {description}")

    time_display = _parse_event_time(event_time) if event_time else None
    if event_time:
        desc_parts.append(f"**Horario:** {time_display or event_time}")

    desc_parts.append(f"**Criador:** <@{creator_id}>")
    desc_parts.append("\u2501" * 30)

    if lfg_role_id := session.get("lfg_role_id"):
        desc_parts.insert(0, f"<@&{lfg_role_id}>")

    embed.description = "\n".join(desc_parts)

    if slots_config:
        await _add_category_fields(embed, slots_config, participants, guild)
    else:
        await _add_flat_field(embed, participants, guild)

    separator_shown = bool(slots_config)

    queued = [p for p in participants if p.get("role") is None]
    if queued:
        if not separator_shown:
            embed.add_field(
                name="\u200b",
                value="\u2501" * 30,
                inline=False,
            )
        q_mentions = await _resolve_mentions(
            [p["user_id"] for p in queued], guild
        )
        q_positions = [p.get("queue_position", i + 1) for i, p in enumerate(queued)]
        q_name, q_value = format_queue_field(q_mentions, q_positions)
        embed.add_field(name=q_name, value=q_value, inline=False)
    elif separator_shown:
        embed.add_field(
            name="\u200b",
            value="\u2501" * 30,
            inline=False,
        )

    embed.add_field(
        name="\u200b",
        value="_Escolha sua funcao no menu para entrar ou va para a fila de espera._",
        inline=False,
    )

    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    if created_at:
        ts = created_at
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                ts = None
        if ts:
            embed.timestamp = ts

    embed.set_footer(
        text="GuildForge",
        icon_url=GUILDFORGE_LOGO_URL,
    )

    return embed


def _resolve_color(
    status: str,
    slots_config: dict,
    participants: list[dict],
) -> discord.Color:
    if status == "closed":
        return discord.Color.dark_grey()
    if status == "cancelled":
        return discord.Color.red()
    if _is_group_full(slots_config, participants):
        return discord.Color.green()
    return GUILDFORGE_COLOR


def _is_group_full(slots_config: dict, participants: list[dict]) -> bool:
    if not slots_config:
        return False
    total_limit = sum(
        cfg.get("limit", 1)
        for cfg in slots_config.values()
        if isinstance(cfg, dict)
    )
    total_filled = len([p for p in participants if p.get("role") is not None])
    return total_filled >= total_limit and total_limit > 0


async def _add_category_fields(
    embed: discord.Embed,
    slots_config: dict,
    participants: list[dict],
    guild: discord.Guild,
) -> None:
    categories: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for role_name, cfg in slots_config.items():
        if not isinstance(cfg, dict):
            continue
        cat = cfg.get("category", "Geral")
        limit = cfg.get("limit", 1)
        categories[cat].append((role_name, limit))

    for cat_name, roles in categories.items():
        cat_lines: list[str] = []
        for role_name, limit in roles:
            occupied = [
                p["user_id"]
                for p in participants
                if p.get("role") == role_name
            ]
            mentions = await _resolve_mentions(occupied, guild)
            field_name, field_value = format_role_field(
                role_name, mentions, limit
            )
            cat_lines.append(f"**{field_name}**")
            cat_lines.append(field_value)

        embed.add_field(
            name=f"\U0001f4cb {cat_name}",
            value="\n".join(cat_lines) if cat_lines else "_Sem vagas_",
            inline=False,
        )


async def _add_flat_field(
    embed: discord.Embed,
    participants: list[dict],
    guild: discord.Guild,
) -> None:
    occupied = [p for p in participants if p.get("role") is not None]
    if not occupied:
        embed.add_field(
            name="\U0001f4cb Participantes",
            value="_Nenhum participante ainda._",
            inline=False,
        )
        return

    mentions = await _resolve_mentions(
        [p["user_id"] for p in occupied], guild
    )
    lines = [f"{_BULLET} {m} \u2014 {p['role']}" for m, p in zip(mentions, occupied)]
    embed.add_field(
        name=f"\U0001f4cb Participantes [{len(occupied)}]",
        value="\n".join(lines) if lines else "_Nenhum participante ainda._",
        inline=False,
    )
