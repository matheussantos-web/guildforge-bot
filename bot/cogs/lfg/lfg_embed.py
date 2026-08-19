from __future__ import annotations

from collections import defaultdict

import discord


def build_lfg_embed(
    session: dict,
    participants: list[dict],
    pending_claims: list[dict],
    guild: discord.Guild,
    lfg_role_id: int | None = None,
) -> discord.Embed:
    status = session["status"]
    color = discord.Color.green() if status == "active" else discord.Color.red()
    embed = discord.Embed(title=session["title"], color=color)

    if session.get("description"):
        embed.description = session["description"]

    if status != "active":
        embed.description = (
            (embed.description or "") + "\n\nEste evento foi **encerrado**."
        ).strip()

    creator_mention = f"<@{session['creator_id']}>"
    embed.add_field(name="Criado por", value=creator_mention, inline=True)

    if session.get("event_time"):
        embed.add_field(name="Horário", value=session["event_time"], inline=True)

    if lfg_role_id:
        embed.add_field(name="Aviso", value=f"<@&{lfg_role_id}>", inline=True)

    slots_config = session.get("slots_config") or {}
    queued = [p for p in participants if p.get("role") is None]
    claimed_user_ids = {c["user_id"] for c in pending_claims}

    if slots_config:
        _add_category_fields(embed, slots_config, participants, guild, claimed_user_ids)
    else:
        _add_flat_field(embed, participants, guild, claimed_user_ids)

    if queued:
        lines = []
        for p in queued:
            pos = p.get("queue_position", "?")
            member = guild.get_member(p["user_id"])
            mention = member.mention if member else f"👤 Indisponível ({p['user_id']})"
            lines.append(f"#{pos} — {mention}")
        embed.add_field(
            name=f"Fila ({len(queued)})",
            value="\n".join(lines),
            inline=False,
        )

    embed.set_footer(text=f"{'Ativo' if status == 'active' else 'Encerrado'}")
    return embed


def _add_category_fields(
    embed: discord.Embed,
    slots_config: dict,
    participants: list[dict],
    guild: discord.Guild,
    claimed_user_ids: set[int],
) -> None:
    categories: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for role_name, cfg in slots_config.items():
        cat = cfg.get("category", "Geral") if isinstance(cfg, dict) else "Geral"
        limit = cfg.get("limit", 1) if isinstance(cfg, dict) else 1
        categories[cat].append((role_name, limit))

    for cat_name, roles in categories.items():
        lines: list[str] = []
        for role_name, limit in roles:
            occupied = [p for p in participants if p.get("role") == role_name]
            count = len(occupied)
            lines.append(f"**{role_name}** ({count}/{limit})")
            for p in occupied:
                member = guild.get_member(p["user_id"])
                if member:
                    mention = member.mention
                else:
                    mention = f"👤 Indisponível ({p['user_id']})"
                marker = " ⏳" if p["user_id"] in claimed_user_ids else ""
                lines.append(f"  {mention}{marker}")
        embed.add_field(name=cat_name, value="\n".join(lines), inline=False)


def _add_flat_field(
    embed: discord.Embed,
    participants: list[dict],
    guild: discord.Guild,
    claimed_user_ids: set[int],
) -> None:
    occupied = [p for p in participants if p.get("role") is not None]
    lines: list[str] = []
    for p in occupied:
        member = guild.get_member(p["user_id"])
        if member:
            mention = member.mention
        else:
            mention = f"👤 Indisponível ({p['user_id']})"
        marker = " ⏳" if p["user_id"] in claimed_user_ids else ""
        lines.append(f"{p['role']}: {mention}{marker}")
    embed.add_field(
        name="Participantes" if lines else "Participantes",
        value="\n".join(lines) if lines else "Nenhum participante ainda.",
        inline=False,
    )
