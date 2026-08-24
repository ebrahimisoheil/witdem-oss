SELECT
    o.execution_id,
    SUM(CAST(json_extract_string(o.attributes, '$.cost_usd') AS DOUBLE)) AS known_cost,
    SUM(CAST(json_extract_string(o.attributes, '$.input_tokens') AS DOUBLE)) AS input_tokens,
    SUM(CAST(json_extract_string(o.attributes, '$.output_tokens') AS DOUBLE)) AS output_tokens,
    SUM(CAST(json_extract_string(o.attributes, '$.total_tokens') AS DOUBLE)) AS total_tokens
FROM operations o
GROUP BY o.execution_id
