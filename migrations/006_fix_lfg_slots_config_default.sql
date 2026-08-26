ALTER TABLE lfg_sessions
    ALTER COLUMN slots_config SET DEFAULT '[]'::jsonb;

UPDATE lfg_sessions
    SET slots_config = '[]'::jsonb
    WHERE slots_config = '{}'::jsonb;
