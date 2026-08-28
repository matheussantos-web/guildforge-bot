from __future__ import annotations

import re
from datetime import datetime, timezone

import discord

from bot.core.branding import (
    GUILDFORGE_COLOR,
    GUILDFORGE_LOGO_URL,
    build_progress_bar,
    get_role_emoji,
)

_MAX_FIELD_CHARS = 1000
_FIELD_LINES_OVERHEAD = 40
_BULLET = "└"


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
            mentions.append(f"👤 Indisponível ({uid})")
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
    name = f"⌛ Fila de Espera `[{count}]`"

    if not queued_mentions:
        return name, "_Ninguém na fila._"

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


def _parse_event_time(raw: str, tz_name: str | None = None) -> str | None:
    if not raw:
        return None
    cleaned = raw.strip()
    m = re.match(r"(\d{1,2}):(\d{2})", cleaned)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            from datetime import timezone as tz_mod
            from zoneinfo import ZoneInfo

            if tz_name:
                try:
                    tz = ZoneInfo(tz_name)
                except (KeyError, ValueError):
                    tz = tz_mod.utc
            else:
                tz = tz_mod.utc
            now = datetime.now(tz_mod.utc)
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0, tzinfo=tz)
            target_utc = target.astimezone(tz_mod.utc)
            if target_utc <= now:
                from datetime import timedelta
                target_utc += timedelta(days=1)
            return f"<t:{int(target_utc.timestamp())}:R>"
    return None


async def build_lfg_embed(
    session: dict,
    participants: list[dict],
    pending_claims: list[dict],
    guild: discord.Guild,
    tz_name: str | None = None,
    guild_display_name: str | None = None,
) -> tuple[discord.Embed, str]:
    status = session["status"]
    slots_config = session.get("slots_config") or []
    creator_id = session["creator_id"]
    title = session.get("title", "LFG")
    description = session.get("description", "")
    event_time = session.get("event_time", "")
    created_at = session.get("created_at")

    color = _resolve_color(status, slots_config, participants)

    embed = discord.Embed(color=color)
    author_name = f"{guild_display_name} • Sistema de LFG" if guild_display_name else f"{guild.name} • Sistema de LFG"
    embed.set_author(name=author_name)

    badge = ""
    if status == "active":
        if not event_time:
            badge = "🟢 ✅" if _is_group_full(slots_config, participants) else "🟢"
        elif _is_group_full(slots_config, participants):
            badge = "✅"
    elif status == "closed":
        badge = "🔒"
    elif status == "cancelled":
        badge = "❌"
    embed.title = f"{badge} ▸ {title}" if badge else f"▸ {title}"

    time_display = _parse_event_time(event_time, tz_name) if event_time else None
    if event_time:
        embed.add_field(
            name="🕒 Horário",
            value=f"{time_display or event_time}",
            inline=True,
        )
    embed.add_field(
        name="👤 Caller",
        value=f"<@{creator_id}>",
        inline=True,
    )

    if description:
        embed.add_field(
            name="📝 Descrição",
            value=description,
            inline=False,
        )
        embed.add_field(
            name="\u200b",
            value="─────────────────",
            inline=False,
        )

    if slots_config:
        total = sum(e.get("limit", 1) for e in slots_config)
        filled = len([p for p in participants if p.get("role") is not None])
        bar = build_progress_bar(filled, total)
        embed.add_field(
            name="📊 Progresso",
            value=f"{bar} {filled}/{total}",
            inline=False,
        )

    if slots_config:
        await _add_all_slots_field(embed, slots_config, participants, guild)
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
        value="_💡 Escolha sua função no menu para entrar ou vá para a fila de espera._",
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

    content = f"<@&{session.get('lfg_role_id')}>" if session.get("lfg_role_id") else ""

    return embed, content


def _resolve_color(
    status: str,
    slots_config: list,
    participants: list[dict],
) -> discord.Color:
    if status == "closed":
        return discord.Color.dark_grey()
    if status == "cancelled":
        return discord.Color.red()
    if _is_group_full(slots_config, participants):
        return discord.Color.green()
    return GUILDFORGE_COLOR


def _is_group_full(slots_config: list, participants: list[dict]) -> bool:
    if not slots_config:
        return False
    total_limit = sum(
        entry.get("limit", 1) for entry in slots_config
    )
    total_filled = len([p for p in participants if p.get("role") is not None])
    return total_filled >= total_limit and total_limit > 0


async def _add_all_slots_field(
    embed: discord.Embed,
    slots_config: list,
    participants: list[dict],
    guild: discord.Guild,
) -> None:
    role_blocks: list[str] = []
    for entry in slots_config:
        role_name = entry.get("role", "")
        limit = entry.get("limit", 1)
        occupied = [
            p["user_id"]
            for p in participants
            if p.get("role") == role_name
        ]
        mentions = await _resolve_mentions(occupied, guild)
        field_name, field_value = format_role_field(
            role_name, mentions, limit
        )
        role_blocks.append(f"**{field_name}**\n{field_value}")

    embed.add_field(
        name="\u200b",
        value=("\n" + "\n\n".join(role_blocks)) if role_blocks else "\n_Sem vagas_",
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
            name="📋 Participantes",
            value="_Nenhum participante ainda._",
            inline=False,
        )
        return

    mentions = await _resolve_mentions(
        [p["user_id"] for p in occupied], guild
    )
    lines = [f"{_BULLET} {m} — {p['role']}" for m, p in zip(mentions, occupied)]
    embed.add_field(
        name=f"📋 Participantes [{len(occupied)}]",
        value="\n".join(lines) if lines else "_Nenhum participante ainda._",
        inline=False,
    )
