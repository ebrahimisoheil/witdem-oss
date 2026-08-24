COALESCE({{alias}}.status, '') != 'running'
AND NOT EXISTS (
    SELECT 1
    FROM operations sf
    WHERE sf.execution_id = {{alias}}.execution_id
      AND sf.status = 'error'
)
