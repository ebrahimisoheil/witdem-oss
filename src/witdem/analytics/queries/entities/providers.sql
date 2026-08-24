SELECT
    json_extract_string(attributes, '$.provider') AS provider,
    COUNT(*) AS calls,
    COUNT(DISTINCT execution_id) AS executions,
    SUM(CAST(json_extract_string(attributes, '$.cost_usd') AS DOUBLE)) AS known_cost
FROM operations
WHERE json_extract_string(attributes, '$.provider') IS NOT NULL
GROUP BY provider
ORDER BY executions DESC, provider
