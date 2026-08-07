CREATE TABLE IF NOT EXISTS guilds (
    id                     BIGINT PRIMARY KEY,
    name                   TEXT NOT NULL,
    member_role_id         BIGINT,
    log_channel_id         BIGINT,
    points_per_hour_voice  INTEGER NOT NULL DEFAULT 10,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS members (
    id                    SERIAL PRIMARY KEY,
    guild_id              BIGINT NOT NULL REFERENCES guilds(id) ON DELETE CASCADE,
    discord_user_id       BIGINT NOT NULL,
    albion_character_name TEXT,
    registered_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (guild_id, discord_user_id)
);

CREATE TABLE IF NOT EXISTS points_transactions (
    id           SERIAL PRIMARY KEY,
    guild_id     BIGINT NOT NULL REFERENCES guilds(id) ON DELETE CASCADE,
    member_id    INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    amount       INTEGER NOT NULL CHECK (amount <> 0),
    reason       TEXT NOT NULL CHECK (reason IN
                   ('voice_time', 'content_event', 'manual_admin', 'shop_purchase')),
    reference_id TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS voice_sessions (
    id            SERIAL PRIMARY KEY,
    guild_id      BIGINT NOT NULL REFERENCES guilds(id) ON DELETE CASCADE,
    member_id     INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    channel_id    BIGINT NOT NULL,
    joined_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_tick_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS content_events (
    id                     SERIAL PRIMARY KEY,
    guild_id               BIGINT NOT NULL REFERENCES guilds(id) ON DELETE CASCADE,
    created_by_member_id   INTEGER NOT NULL REFERENCES members(id),
    type                   TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'open'
                             CHECK (status IN ('open', 'closed', 'cancelled')),
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at              TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS content_participants (
    event_id   INTEGER NOT NULL REFERENCES content_events(id) ON DELETE CASCADE,
    member_id  INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    joined_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (event_id, member_id)
);

CREATE INDEX IF NOT EXISTS idx_points_transactions_guild_member
    ON points_transactions (guild_id, member_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_points_transactions_reference
    ON points_transactions (guild_id, reference_id)
    WHERE reference_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_voice_sessions_guild_member
    ON voice_sessions (guild_id, member_id);

CREATE INDEX IF NOT EXISTS idx_content_events_open
    ON content_events (guild_id)
    WHERE status = 'open';
