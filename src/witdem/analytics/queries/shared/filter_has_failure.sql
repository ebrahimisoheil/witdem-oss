EXISTS (
    SELECT 1
    FROM operations f
    WHERE f.execution_id = {{alias}}.execution_id
      AND f.status = 'error'
)
