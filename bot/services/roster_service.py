import logging
from typing import Any

import asyncpg
import discord

from bot.core.guild_settings import get_guild_config
from bot.services.albion_api_service import AlbionAPIError, fetch_guild_members

log = logging.getLogger(__name__)

ROSTER_UPSERT = """
    INSERT INTO albion_roster (guild_id, albion_character_id, character_name, synced_at)
    VALUES ($1, $2, $3, now())
    ON CONFLICT (guild_id, albion_character_id)
    DO UPDATE SET character_name = EXCLUDED.character_name, synced_at = now()
"""

LOCAL_MEMBERS_FETCH = """
    SELECT id, discord_user_id, albion_character_name
    FROM members
    WHERE guild_id = $1 AND albion_character_name IS NOT NULL
"""


async def sync_guild_roster(bot: discord.Client, pool: asyncpg.Pool, guild_id: int) -> dict[str, Any]:
    config = await get_guild_config(pool, guild_id)
    albion_guild_id = config.get("albion_guild_id") if config else None
    if not albion_guild_id:
        log.info("Guilda %s sem albion_guild_id configurado; varredura pulada", guild_id)
        return {"roster": 0, "revoked": 0}

    try:
        fresh = await fetch_guild_members(albion_guild_id)
    except AlbionAPIError as exc:
        log.error("Varredura da guilda %s abortada (API instável): %s", guild_id, exc)
        return {"roster": 0, "revoked": 0}

    if not fresh:
        log.warning(
            "API devolveu roster vazio para a guilda %s; varredura abortada "
            "para não revogar cargos por engano",
            guild_id,
        )
        return {"roster": 0, "revoked": 0}

    async with pool.acquire() as conn:
        for member in fresh:
            await conn.execute(
                ROSTER_UPSERT,
                guild_id,
                member["id"],
                member["name"],
            )

    fresh_names = {member["name"].lower() for member in fresh}

    async with pool.acquire() as conn:
        local_members = await conn.fetch(LOCAL_MEMBERS_FETCH, guild_id)

    guild = bot.get_guild(guild_id)
    if guild is None:
        log.warning("Bot não está no servidor %s; cargos não ajustados", guild_id)
        return {"roster": len(fresh), "revoked": 0}

    member_role = guild.get_role(config.get("member_role_id")) if config.get("member_role_id") else None
    default_role = guild.get_role(config.get("default_role_id")) if config.get("default_role_id") else None

    revoked = 0
    if member_role is not None:
        for row in local_members:
            name = row["albion_character_name"]
            if not name or name.lower() in fresh_names:
                continue

            discord_member = guild.get_member(row["discord_user_id"])
            if discord_member is None:
                continue
            if member_role not in discord_member.roles:
                continue

            try:
                if default_role is not None:
                    await discord_member.add_roles(
                        default_role,
                        reason="Removido do roster da guilda Albion",
                    )
                await discord_member.remove_roles(
                    member_role,
                    reason="Removido do roster da guilda Albion",
                )
                revoked += 1
            except (discord.Forbidden, discord.HTTPException):
                log.warning(
                    "Sem permissão para ajustar cargos de %s na guilda %s",
                    discord_member,
                    guild_id,
                )

    log.info(
        "Varredura da guilda %s: %d membro(s) no roster, %d cargo(s) revogado(s)",
        guild_id,
        len(fresh),
        revoked,
    )
    return {"roster": len(fresh), "revoked": revoked}
