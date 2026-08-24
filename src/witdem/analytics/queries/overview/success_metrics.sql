SELECT
    e.execution_id,
    e.status AS runtime_status,
    bo.status AS business_outcome,
    COALESCE(
        json_extract_string(ev.payload, '$.status'),
        json_extract_string(ev.payload, '$.acceptance_reason')
    ) AS runtime_outcome
FROM executions e
LEFT JOIN outcomes bo ON bo.execution_id = e.execution_id
LEFT JOIN events ev ON ev.execution_id = e.execution_id AND ev.type = 'outcome'
