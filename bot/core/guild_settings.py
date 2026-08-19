from typing import Any

import asyncpg

_cache: dict[int, dict[str, Any]] = {}
_settings_cache: dict[int, dict[str, str]] = {}

ALLOWED_COLUMNS = {
    "name",
    "member_role_id",
    "log_channel_id",
    "points_per_hour_voice",
    "albion_guild_id",
    "albion_guild_name",
    "default_role_id",
    "lfg_notify_role_id",
}


def _invalidate(guild_id: int) -> None:
    _cache.pop(guild_id, None)
    _settings_cache.pop(guild_id, None)


async def get_guild_config(pool: asyncpg.Pool, guild_id: int) -> dict[str, Any] | None:
    cached = _cache.get(guild_id)
    if cached is not None:
        return cached

    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM guilds WHERE id = $1", guild_id)
    if row is None:
        return None

    data = dict(row)
    _cache[guild_id] = data
    return data


async def upsert_guild_config(pool: asyncpg.Pool, guild_id: int, **campos: Any) -> None:
    unknown = set(campos) - ALLOWED_COLUMNS
    if unknown:
        raise ValueError(f"Colunas desconhecidas para guilds: {sorted(unknown)}")
    if not campos:
        raise ValueError("Nenhum campo informado para upsert de guild")

    columns = list(campos)
    values = list(campos.values())

    if "name" in campos:
        placeholders = ", ".join(f"${i + 2}" for i in range(len(values)))
        col_sql = ", ".join(columns)
        update_sql = ", ".join(f"{col} = EXCLUDED.{col}" for col in columns)

        sql = f"""
            INSERT INTO guilds (id, {col_sql})
            VALUES ($1, {placeholders})
            ON CONFLICT (id) DO UPDATE SET {update_sql}
        """
        async with pool.acquire() as conn:
            await conn.execute(sql, guild_id, *values)
    else:
        if await get_guild_config(pool, guild_id) is None:
            raise ValueError("name é obrigatório ao criar uma guilda nova")

        set_sql = ", ".join(f"{col} = ${i + 1}" for i, col in enumerate(columns))
        sql = f"UPDATE guilds SET {set_sql} WHERE id = ${len(columns) + 1}"
        async with pool.acquire() as conn:
            await conn.execute(sql, *values, guild_id)

    _invalidate(guild_id)


async def get_setting(
    pool: asyncpg.Pool,
    guild_id: int,
    key: str,
    default: str | None = None,
) -> str | None:
    settings = _settings_cache.get(guild_id)
    if settings is None:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT key, value FROM guild_settings WHERE guild_id = $1",
                guild_id,
            )
        settings = {row["key"]: row["value"] for row in rows}
        _settings_cache[guild_id] = settings
    return settings.get(key, default)


async def set_setting(pool: asyncpg.Pool, guild_id: int, key: str, value: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO guild_settings (guild_id, key, value)
            VALUES ($1, $2, $3)
            ON CONFLICT (guild_id, key) DO UPDATE SET value = EXCLUDED.value
            """,
            guild_id,
            key,
            value,
        )
    _invalidate(guild_id)
