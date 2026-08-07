CREATE TABLE IF NOT EXISTS guild_settings (
    id         SERIAL PRIMARY KEY,
    guild_id   BIGINT NOT NULL REFERENCES guilds(id) ON DELETE CASCADE,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    UNIQUE (guild_id, key)
);
