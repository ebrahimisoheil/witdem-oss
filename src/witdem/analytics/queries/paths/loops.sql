SELECT execution_id, operation_id, kind, name, attempt, started_at, ended_at, attributes
FROM operations
WHERE attempt IS NOT NULL AND attempt > 1
ORDER BY execution_id, started_at
