SELECT
    COUNT(*) FILTER (WHERE started_at IS NOT NULL AND ended_at IS NOT NULL) AS timed_operations,
    COUNT(*) FILTER (WHERE status = 'error') AS error_operations,
    COUNT(*) FILTER (WHERE kind = 'model') AS model_operations,
    COUNT(*) FILTER (WHERE kind = 'tool') AS tool_operations,
    COUNT(*) FILTER (WHERE kind = 'tool' AND json_extract_string(attributes, '$.cost_usd') IS NOT NULL) AS measured_tool_cost_operations,
    COUNT(*) FILTER (WHERE json_extract_string(attributes, '$.provider') IS NOT NULL) AS provider_operations,
    COUNT(*) FILTER (WHERE json_extract_string(attributes, '$.model') IS NOT NULL) AS model_identity_operations,
    COUNT(*) FILTER (WHERE json_extract_string(attributes, '$.total_tokens') IS NOT NULL) AS token_operations,
    COUNT(*) FILTER (WHERE json_extract_string(attributes, '$.cost_usd') IS NOT NULL) AS cost_operations,
    COUNT(*) FILTER (WHERE json_extract_string(attributes, '$.role') IS NOT NULL) AS role_operations
FROM operations
