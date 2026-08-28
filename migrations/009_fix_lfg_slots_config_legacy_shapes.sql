-- Corrige slots_config de formato legado (fase 2).
-- Antes do commit 6eab5a6 o slots_config era um objeto: {"Tank": {"limit": 1, "category": "Geral"}}.
-- Corrige também arrays com entradas não-dict. Idempotente: arrays já válidos não são alterados.

-- 1) slots_config inteiro como string -> array com a string
UPDATE lfg_sessions
SET slots_config = jsonb_build_array(slots_config)
WHERE jsonb_typeof(slots_config) = 'string';

-- 2) objeto -> array de dicts {role, limit, category}
UPDATE lfg_sessions
SET slots_config = (
    SELECT jsonb_agg(
        jsonb_build_object(
            'role', k,
            'limit',
            CASE
                WHEN jsonb_typeof(v) = 'number' THEN v
                ELSE to_jsonb(COALESCE((v ->> 'limit')::int, 1))
            END,
            'category', COALESCE(v ->> 'category', 'Geral')
        )
    )
    FROM jsonb_each(slots_config) AS kv(k, v)
)
WHERE jsonb_typeof(slots_config) = 'object';

-- 3) array com entradas que nao sao dict -> dicts {role, limit}
-- (cobre DBs onde a migracao 008 ainda nao rodou; arrays validos ficam intactos)
UPDATE lfg_sessions
SET slots_config = (
    SELECT jsonb_agg(
        CASE
            WHEN jsonb_typeof(elem) = 'string' OR jsonb_typeof(elem) = 'number'
                 OR jsonb_typeof(elem) = 'boolean' OR jsonb_typeof(elem) = 'null'
                 OR jsonb_typeof(elem) = 'array'
                THEN jsonb_build_object('role', elem, 'limit', 1)
            ELSE elem
        END
    )
    FROM jsonb_array_elements(slots_config) AS elem
)
WHERE jsonb_typeof(slots_config) = 'array'
  AND EXISTS (
      SELECT 1
      FROM jsonb_array_elements(slots_config) AS e
      WHERE jsonb_typeof(e) <> 'object'
  );