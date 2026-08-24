SELECT
    execution_id,
    operation_id,
    kind,
    name,
    started_at,
    ended_at,
    attributes
FROM operations
WHERE status = 'error'
ORDER BY started_at
