from __future__ import annotations

import re
from datetime import datetime, timezone

import discord

from bot.core.branding import (
    GUILDFORGE_COLOR,
    GUILDFORGE_LOGO_URL,
    build_progress_bar,
)

_MAX_FIELD_CHARS = 1000
_FIELD_LINES_OVERHEAD = 40
_BULLET = "└"


def _slot_entry(entry: object) -> tuple[str, int]:
    if isinstance(entry, dict):
        role = str(entry.get("role", "") or "")
        limit = entry.get("limit", 1)
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 1
        return role, max(1, limit)
    return str(entry), 1


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
    count = len(member_mentions)
    name = f"🔹 `{role_name}` [{count}/{limit}]"

    if count == 0:
        if limit <= 1:
            return name, "🟢 *Livre*"
        return name, f"🟢 *{limit} vagas livres*"

    shown = member_mentions[:max_shown]
    remaining = count - len(shown)
    free = max(0, limit - count)

    def _free_line() -> list[str]:
        if free == 1:
            return ["🟢 *Livre*"]
        if free > 1:
            return [f"🟢 *{free} vagas livres*"]
        return []

    lines = [f"🔴 {m}" for m in shown]
    if remaining > 0:
        lines.append(f"🔴 *e mais {remaining}...*")
    lines += _free_line()

    value = "\n".join(lines)
    while len(value) > _MAX_FIELD_CHARS and len(shown) > 1:
        shown = shown[:-1]
        remaining = count - len(shown)
        lines = [f"🔴 {m}" for m in shown]
        if remaining > 0:
            lines.append(f"🔴 *e mais {remaining}...*")
        lines += _free_line()
        value = "\n".join(lines)

    return name, value


def format_queue_field(
    queued_mentions: list[str],
    max_shown: int = 20,
) -> tuple[str, str]:
    name = "⏳ Fila de Espera"

    if not queued_mentions:
        return name, "_Ninguém na fila._"

    shown = queued_mentions[:max_shown]
    remaining = len(queued_mentions) - len(shown)

    parts = ", ".join(shown)
    if remaining > 0:
        parts += f", *e mais {remaining}...*"

    return name, parts


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

    guild_label = guild_display_name or guild.name
    embed.title = f"👑 {guild_label} • Sistema de LFG"

    desc_parts = [f"# ⚔️ {title}"]
    if description:
        desc_parts.append(f"📜 **Requisitos:** `{' '.join(description.split())}`")
    embed.description = "\n".join(desc_parts)

    _add_spacer(embed)

    embed.add_field(
        name="👑 Caller",
        value=f"<@{creator_id}>",
        inline=True,
    )

    time_display = _parse_event_time(event_time, tz_name) if event_time else None
    if event_time:
        embed.add_field(
            name="⏰ Horário",
            value=f"{time_display or event_time}",
            inline=True,
        )

    _add_spacer(embed)

    if slots_config:
        total = sum(_slot_entry(e)[1] for e in slots_config)
        filled = len([p for p in participants if p.get("role") is not None])
        pct = round(filled / total * 100) if total else 0
        bar = build_progress_bar(filled, total)
        embed.add_field(
            name="📊 Progresso do Grupo",
            value=f"{bar} {pct}% Concluído",
            inline=False,
        )

    _add_spacer(embed)

    if slots_config:
        await _add_all_slots_field(embed, slots_config, participants, guild)
    else:
        await _add_flat_field(embed, participants, guild)

    queued = [p for p in participants if p.get("role") is None]
    if queued:
        _add_spacer(embed)
        q_mentions = await _resolve_mentions(
            [p["user_id"] for p in queued], guild
        )
        q_name, q_value = format_queue_field(q_mentions)
        embed.add_field(name=q_name, value=q_value, inline=False)

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
        text="💡 Selecione sua função no menu abaixo para entrar ou sair da fila.",
        icon_url=guild.icon.url if guild.icon else GUILDFORGE_LOGO_URL,
    )

    content = f"<@&{session.get('lfg_role_id')}>" if session.get("lfg_role_id") else ""

    return embed, content


def _add_spacer(embed: discord.Embed) -> None:
    embed.add_field(name="\u200b", value="\u200b", inline=False)


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
        _slot_entry(entry)[1] for entry in slots_config
    )
    total_filled = len([p for p in participants if p.get("role") is not None])
    return total_filled >= total_limit and total_limit > 0


async def _add_all_slots_field(
    embed: discord.Embed,
    slots_config: list,
    participants: list[dict],
    guild: discord.Guild,
) -> None:
    added = 0
    for entry in slots_config:
        role_name, limit = _slot_entry(entry)
        occupied = [
            p["user_id"]
            for p in participants
            if p.get("role") == role_name
        ]
        mentions = await _resolve_mentions(occupied, guild)
        field_name, field_value = format_role_field(
            role_name, mentions, limit
        )
        embed.add_field(
            name=field_name,
            value=field_value,
            inline=True,
        )
        added += 1

    padding = (3 - added % 3) % 3
    for _ in range(padding):
        embed.add_field(
            name="\u200b",
            value="\u200b",
            inline=True,
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
