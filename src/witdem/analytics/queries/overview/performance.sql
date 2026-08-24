SELECT
    execution_id,
    kind,
    name,
    started_at,
    ended_at,
    date_diff('millisecond', CAST(started_at AS TIMESTAMP), CAST(ended_at AS TIMESTAMP)) / 1000.0 AS duration_seconds
FROM operations
WHERE started_at IS NOT NULL AND ended_at IS NOT NULL
