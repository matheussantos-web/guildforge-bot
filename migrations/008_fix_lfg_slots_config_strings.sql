-- Corrige slots_config de formato legado.
-- Sessões antigas podem ter entradas como string simples (ex: ["tank"])
-- em vez de dict (ex: [{"role": "tank", "limit": 1}]).

UPDATE lfg_sessions
SET slots_config = jsonb_build_array(slots_config)
WHERE jsonb_typeof(slots_config) = 'string';

UPDATE lfg_sessions
SET slots_config = (
    SELECT jsonb_agg(
        CASE
            WHEN jsonb_typeof(elem) = 'string'
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
      WHERE jsonb_typeof(e) = 'string'
  );