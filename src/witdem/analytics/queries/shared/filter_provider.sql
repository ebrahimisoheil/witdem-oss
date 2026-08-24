EXISTS (
    SELECT 1
    FROM operations p
    WHERE p.execution_id = {{alias}}.execution_id
      AND json_extract_string(p.attributes, '$.provider') = ?
)
