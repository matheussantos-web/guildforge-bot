from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import asyncpg

log = logging.getLogger(__name__)

_SESSION_INSERT = """
    INSERT INTO lfg_sessions (guild_id, message_id, channel_id, creator_id,
                              title, description, event_time, slots_config)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
    RETURNING id
"""

_SESSION_BY_MESSAGE = """
    SELECT id, guild_id, message_id, channel_id, creator_id,
           title, description, event_time, slots_config, status, created_at,
           warning_sent_at
    FROM lfg_sessions
    WHERE message_id = $1
"""

_SESSION_BY_ID = """
    SELECT id, guild_id, message_id, channel_id, creator_id,
           title, description, event_time, slots_config, status, created_at,
           warning_sent_at
    FROM lfg_sessions
    WHERE id = $1
"""

_SESSIONS_ACTIVE = """
    SELECT id, guild_id, message_id, channel_id, creator_id,
           title, description, event_time, slots_config, status, created_at,
           warning_sent_at
    FROM lfg_sessions
    WHERE status = 'active'
"""

_SESSIONS_LIVE_UNTIMED = """
    SELECT id, guild_id, message_id, channel_id, creator_id,
           title, description, event_time, slots_config, status, created_at,
           warning_sent_at
    FROM lfg_sessions
    WHERE status = 'active' AND event_time = ''
"""

_SESSION_UPDATE_STATUS = """
    UPDATE lfg_sessions SET status = $2 WHERE id = $1
"""

_SESSION_UPDATE_MESSAGE = """
    UPDATE lfg_sessions SET message_id = $2 WHERE id = $1
"""

_PARTICIPANTS_BY_SESSION = """
    SELECT user_id, role, queue_position, joined_at
    FROM lfg_participants
    WHERE session_id = $1
    ORDER BY role NULLS LAST, queue_position NULLS LAST, joined_at
"""

_PARTICIPANT_UPSERT = """
    INSERT INTO lfg_participants (session_id, user_id, role, queue_position)
    VALUES ($1, $2, $3, NULL)
    ON CONFLICT (session_id, user_id) DO UPDATE
      SET role = EXCLUDED.role,
          queue_position = NULL,
          joined_at = now()
"""

_QUEUE_UPSERT = """
    INSERT INTO lfg_participants (session_id, user_id, role, queue_position)
    VALUES ($1, $2, NULL, $3)
    ON CONFLICT (session_id, user_id) DO UPDATE
      SET role = NULL,
          queue_position = EXCLUDED.queue_position,
          joined_at = now()
"""

_PARTICIPANT_REMOVE = """
    DELETE FROM lfg_participants
    WHERE session_id = $1 AND user_id = $2
    RETURNING role
"""

_CLAIM_INSERT = """
    INSERT INTO lfg_pending_claims (session_id, user_id, role, expires_at)
    VALUES ($1, $2, $3, $4)
"""

_CLAIM_RESOLVE = """
    UPDATE lfg_pending_claims
    SET resolved = true
    WHERE session_id = $1 AND user_id = $2 AND resolved = false
"""

_CLAIMS_EXPIRED = """
    SELECT id, session_id, user_id, role, expires_at
    FROM lfg_pending_claims
    WHERE resolved = false AND expires_at <= now()
"""


async def create_session(
    pool: asyncpg.Pool,
    guild_id: int,
    message_id: int | None,
    channel_id: int,
    creator_id: int,
    title: str,
    description: str,
    event_time: str,
    slots_config: dict[str, Any],
) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            _SESSION_INSERT,
            guild_id,
            message_id,
            channel_id,
            creator_id,
            title,
            description,
            event_time,
            slots_config,
        )


async def get_session_by_message_id(
    pool: asyncpg.Pool, message_id: int
) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        session = await conn.fetchrow(_SESSION_BY_MESSAGE, message_id)
        if session is None:
            return None
        participants = await conn.fetch(
            _PARTICIPANTS_BY_SESSION, session["id"]
        )
        claims = await conn.fetch(
            "SELECT user_id, role, expires_at FROM lfg_pending_claims "
            "WHERE session_id = $1 AND resolved = false",
            session["id"],
        )
    return {
        "session": dict(session),
        "participants": [dict(p) for p in participants],
        "pending_claims": [dict(c) for c in claims],
    }


async def get_session_by_id(
    pool: asyncpg.Pool, session_id: int
) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        session = await conn.fetchrow(_SESSION_BY_ID, session_id)
        if session is None:
            return None
        participants = await conn.fetch(
            _PARTICIPANTS_BY_SESSION, session["id"]
        )
        claims = await conn.fetch(
            "SELECT user_id, role, expires_at FROM lfg_pending_claims "
            "WHERE session_id = $1 AND resolved = false",
            session["id"],
        )
    return {
        "session": dict(session),
        "participants": [dict(p) for p in participants],
        "pending_claims": [dict(c) for c in claims],
    }


async def update_session_status(
    pool: asyncpg.Pool, session_id: int, status: str
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_SESSION_UPDATE_STATUS, session_id, status)


async def update_session_message(
    pool: asyncpg.Pool, session_id: int, message_id: int
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_SESSION_UPDATE_MESSAGE, session_id, message_id)


async def update_session_meta(
    pool: asyncpg.Pool,
    session_id: int,
    *,
    title: str,
    description: str,
    event_time: str,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE lfg_sessions "
            "SET title = $2, description = $3, event_time = $4 "
            "WHERE id = $1",
            session_id,
            title,
            description,
            event_time,
        )


async def update_session_slots(
    pool: asyncpg.Pool,
    session_id: int,
    slots_config: list[dict],
    removed_roles: list[str],
) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE lfg_sessions SET slots_config = $2 WHERE id = $1",
                session_id,
                slots_config,
            )
            if removed_roles:
                await conn.execute(
                    "DELETE FROM lfg_participants "
                    "WHERE session_id = $1 AND role = ANY($2)",
                    session_id,
                    removed_roles,
                )


async def list_active_sessions(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetch(_SESSIONS_ACTIVE)


async def get_participants(
    pool: asyncpg.Pool, session_id: int
) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetch(_PARTICIPANTS_BY_SESSION, session_id)


async def upsert_participant(
    pool: asyncpg.Pool, session_id: int, user_id: int, role: str
) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM lfg_participants "
                "WHERE session_id = $1 AND user_id = $2",
                session_id,
                user_id,
            )
            await conn.execute(_PARTICIPANT_UPSERT, session_id, user_id, role)


async def queue_participant(
    pool: asyncpg.Pool, session_id: int, user_id: int
) -> int:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM lfg_participants "
                "WHERE session_id = $1 AND user_id = $2",
                session_id,
                user_id,
            )
            max_pos = await conn.fetchval(
                "SELECT COALESCE(MAX(queue_position), 0) "
                "FROM lfg_participants "
                "WHERE session_id = $1 AND role IS NULL",
                session_id,
            )
            position = max_pos + 1
            await conn.execute(_QUEUE_UPSERT, session_id, user_id, position)
    return position


async def remove_participant(
    pool: asyncpg.Pool, session_id: int, user_id: int
) -> str | None:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            _PARTICIPANT_REMOVE, session_id, user_id
        )


async def create_pending_claim(
    pool: asyncpg.Pool,
    session_id: int,
    user_id: int,
    role: str,
    expires_at: datetime,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_CLAIM_INSERT, session_id, user_id, role, expires_at)


async def resolve_pending_claim(
    pool: asyncpg.Pool, session_id: int, user_id: int
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_CLAIM_RESOLVE, session_id, user_id)


async def get_expired_unresolved_claims(
    pool: asyncpg.Pool,
) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetch(_CLAIMS_EXPIRED)


async def list_live_untimed_sessions(
    pool: asyncpg.Pool,
) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetch(_SESSIONS_LIVE_UNTIMED)


async def list_pending_warn_sessions(
    pool: asyncpg.Pool,
) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT id, guild_id, message_id, channel_id, creator_id,
                   title, description, event_time, slots_config,
                   status, created_at, warning_sent_at
            FROM lfg_sessions
            WHERE status = 'active' AND warning_sent_at IS NOT NULL
            """
        )


async def set_warning_sent_at(
    pool: asyncpg.Pool,
    session_id: int,
    sent_at: datetime | None,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE lfg_sessions SET warning_sent_at = $2 WHERE id = $1",
            session_id,
            sent_at,
        )
