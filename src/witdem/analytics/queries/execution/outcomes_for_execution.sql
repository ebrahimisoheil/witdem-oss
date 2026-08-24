SELECT *
FROM outcomes
WHERE execution_id = ?
ORDER BY timestamp, outcome_id
