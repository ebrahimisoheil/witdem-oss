SELECT
    COUNT(*) FILTER (WHERE type = 'step') AS semantic_stage_events,
    COUNT(*) AS semantic_events
FROM events
