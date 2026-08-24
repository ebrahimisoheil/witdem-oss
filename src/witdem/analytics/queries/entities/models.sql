SELECT
    json_extract_string(attributes, '$.model') AS model,
    COUNT(*) AS calls,
    COUNT(DISTINCT execution_id) AS executions,
    SUM(CAST(json_extract_string(attributes, '$.cost_usd') AS DOUBLE)) AS known_cost,
    SUM(CAST(json_extract_string(attributes, '$.total_tokens') AS DOUBLE)) AS total_tokens
FROM operations
WHERE kind = 'model' AND json_extract_string(attributes, '$.model') IS NOT NULL
GROUP BY model
ORDER BY executions DESC, model
