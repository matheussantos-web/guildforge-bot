"""Helpers de autorização de comandos baseados na configuração da guilda."""

from __future__ import annotations

from typing import Any, TypedDict

import discord


class GuildConfig(TypedDict, total=False):
    """Estrutura documentada das colunas de ``guilds`` usadas por permissões."""

    name: str
    member_role_id: int
    log_channel_id: int
    points_per_hour_voice: int
    albion_guild_id: str
    albion_guild_name: str
    default_role_id: int
    lfg_notify_role_id: str
    guild_timezone: str


def has_member_role(config: GuildConfig | None, member: discord.Member) -> bool:
    """Retorna True se ``member`` possui o cargo de membro configurado."""
    if config is None or not config.get("member_role_id"):
        return False
    member_role_id: Any = config["member_role_id"]
    return member.get_role(int(member_role_id)) is not None
