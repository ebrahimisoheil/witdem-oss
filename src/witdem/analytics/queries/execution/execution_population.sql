SELECT o.*
FROM operations o
JOIN executions e ON e.execution_id = o.execution_id
WHERE {{where}}
