SELECT execution_id, operation_id, kind, name, started_at, ended_at, status, attributes
FROM operations
ORDER BY execution_id, started_at
