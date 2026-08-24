SELECT COALESCE(
    json_extract_string(payload, '$.status'),
    json_extract_string(payload, '$.acceptance_reason')
) AS outcome
FROM events
WHERE execution_id = ? AND type = 'outcome'
ORDER BY timestamp DESC
LIMIT 1
