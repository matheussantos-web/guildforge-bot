CREATE TABLE IF NOT EXISTS lfg_sessions (
    id            SERIAL PRIMARY KEY,
    guild_id      BIGINT NOT NULL REFERENCES guilds(id) ON DELETE CASCADE,
    message_id    BIGINT UNIQUE,
    channel_id    BIGINT NOT NULL,
    creator_id    BIGINT NOT NULL,
    title         TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    event_time    TEXT NOT NULL DEFAULT '',
    slots_config  JSONB NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'closed', 'cancelled')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_lfg_sessions_guild
    ON lfg_sessions (guild_id);

CREATE TABLE IF NOT EXISTS lfg_participants (
    id              SERIAL PRIMARY KEY,
    session_id      INTEGER NOT NULL REFERENCES lfg_sessions(id) ON DELETE CASCADE,
    user_id         BIGINT NOT NULL,
    role            TEXT,
    queue_position  INTEGER,
    joined_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (session_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_lfg_participants_session
    ON lfg_participants (session_id);

CREATE TABLE IF NOT EXISTS lfg_pending_claims (
    id           SERIAL PRIMARY KEY,
    session_id   INTEGER NOT NULL REFERENCES lfg_sessions(id) ON DELETE CASCADE,
    user_id      BIGINT NOT NULL,
    role         TEXT NOT NULL,
    expires_at   TIMESTAMPTZ NOT NULL,
    resolved     BOOLEAN NOT NULL DEFAULT false
);

CREATE INDEX IF NOT EXISTS idx_lfg_pending_claims_unresolved
    ON lfg_pending_claims (session_id)
    WHERE resolved = false;
