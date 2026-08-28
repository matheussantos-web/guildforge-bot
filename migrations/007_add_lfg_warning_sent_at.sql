ALTER TABLE lfg_sessions
    ADD COLUMN IF NOT EXISTS warning_sent_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_lfg_sessions_stale
    ON lfg_sessions (status, event_time, warning_sent_at);