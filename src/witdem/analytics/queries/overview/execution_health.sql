SELECT e.execution_id, e.runtime_id, e.started_at, e.ended_at, e.status, e.schema_version, e.attributes,
  date_diff('millisecond', CAST(e.started_at AS TIMESTAMP), CAST(e.ended_at AS TIMESTAMP)) / 1000.0 AS duration_seconds,
  (SELECT COUNT(*) FROM operations o WHERE o.execution_id=e.execution_id) AS operation_count,
  (SELECT COUNT(*) FROM operations o WHERE o.execution_id=e.execution_id AND o.kind='model') AS model_calls,
  (SELECT COUNT(*) FROM operations o WHERE o.execution_id=e.execution_id AND o.kind='tool') AS tool_calls,
  (SELECT COUNT(*) FROM operations o WHERE o.execution_id=e.execution_id AND o.status='error') AS failure_count,
  0 AS repeated_work,
  (SELECT SUM(CAST(json_extract_string(o.attributes, '$.cost_usd') AS DOUBLE)) FROM operations o WHERE o.execution_id=e.execution_id) AS known_cost,
  (SELECT SUM(CAST(json_extract_string(o.attributes, '$.cost_usd') AS DOUBLE)) FROM operations o WHERE o.execution_id=e.execution_id AND o.kind='model') AS model_cost,
  (SELECT SUM(CAST(json_extract_string(o.attributes, '$.cost_usd') AS DOUBLE)) FROM operations o WHERE o.execution_id=e.execution_id AND o.kind='tool') AS tool_cost,
  (SELECT SUM(CAST(json_extract_string(o.attributes, '$.input_tokens') AS DOUBLE)) FROM operations o WHERE o.execution_id=e.execution_id) AS input_tokens,
  (SELECT SUM(CAST(json_extract_string(o.attributes, '$.output_tokens') AS DOUBLE)) FROM operations o WHERE o.execution_id=e.execution_id) AS output_tokens,
  (SELECT SUM(CAST(json_extract_string(o.attributes, '$.total_tokens') AS DOUBLE)) FROM operations o WHERE o.execution_id=e.execution_id) AS total_tokens,
  (SELECT COUNT(*) FROM operations o WHERE o.execution_id=e.execution_id AND o.kind IN ('model','tool') AND json_extract_string(o.attributes, '$.cost_usd') IS NOT NULL) AS measured_cost_operations,
  (SELECT COUNT(*) FROM operations o WHERE o.execution_id=e.execution_id AND o.kind IN ('model','tool') AND json_extract_string(o.attributes, '$.cost_usd') IS NULL) AS unmeasured_cost_operations,
  (SELECT string_agg(DISTINCT json_extract_string(o.attributes, '$.provider'), ', ' ORDER BY json_extract_string(o.attributes, '$.provider')) FROM operations o WHERE o.execution_id=e.execution_id AND json_extract_string(o.attributes, '$.provider') IS NOT NULL) AS providers,
  (SELECT string_agg(DISTINCT json_extract_string(o.attributes, '$.model'), ', ' ORDER BY json_extract_string(o.attributes, '$.model')) FROM operations o WHERE o.execution_id=e.execution_id AND json_extract_string(o.attributes, '$.model') IS NOT NULL) AS models,
  (SELECT string_agg(DISTINCT COALESCE(json_extract_string(o.attributes, '$.haystack.tool.name'), o.name), ', ' ORDER BY COALESCE(json_extract_string(o.attributes, '$.haystack.tool.name'), o.name)) FROM operations o WHERE o.execution_id=e.execution_id AND o.kind='tool') AS tools,
  (SELECT status FROM outcomes bo WHERE bo.execution_id=e.execution_id AND bo.name NOT IN ('execution.completed', 'product_goal') ORDER BY CASE WHEN bo.name='application_outcome' THEN 0 ELSE 1 END, bo.timestamp DESC LIMIT 1) AS business_outcome,
  (SELECT COALESCE(json_extract_string(ev.payload, '$.status'), json_extract_string(ev.payload, '$.acceptance_reason')) FROM events ev WHERE ev.execution_id=e.execution_id AND ev.type='outcome' ORDER BY ev.timestamp DESC LIMIT 1) AS runtime_outcome,
  COALESCE(
    (SELECT status FROM outcomes bo WHERE bo.execution_id=e.execution_id AND bo.name NOT IN ('execution.completed', 'product_goal') ORDER BY CASE WHEN bo.name='application_outcome' THEN 0 ELSE 1 END, bo.timestamp DESC LIMIT 1),
    (SELECT COALESCE(json_extract_string(ev.payload, '$.status'), json_extract_string(ev.payload, '$.acceptance_reason')) FROM events ev WHERE ev.execution_id=e.execution_id AND ev.type='outcome' ORDER BY ev.timestamp DESC LIMIT 1)
  ) AS outcome,
  COALESCE(
    (SELECT o.name FROM operations o WHERE o.execution_id=e.execution_id AND o.status='error' AND o.kind NOT IN ('workflow','pipeline','agent') ORDER BY o.started_at LIMIT 1),
    (SELECT o.name FROM operations o WHERE o.execution_id=e.execution_id AND o.status='error' ORDER BY o.started_at LIMIT 1)
  ) AS failure_location
FROM executions e
WHERE {{where}}
ORDER BY e.started_at DESC{{limit_clause}}
