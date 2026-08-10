ALTER TABLE guilds
    ADD COLUMN IF NOT EXISTS albion_guild_id TEXT,
    ADD COLUMN IF NOT EXISTS albion_guild_name TEXT,
    ADD COLUMN IF NOT EXISTS default_role_id BIGINT;

CREATE TABLE IF NOT EXISTS albion_roster (
    id                   SERIAL PRIMARY KEY,
    guild_id             BIGINT NOT NULL REFERENCES guilds(id) ON DELETE CASCADE,
    albion_character_id  TEXT NOT NULL,
    character_name       TEXT NOT NULL,
    synced_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (guild_id, albion_character_id)
);

CREATE INDEX IF NOT EXISTS idx_albion_roster_guild_name
    ON albion_roster (guild_id, LOWER(character_name));
