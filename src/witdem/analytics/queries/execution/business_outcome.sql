SELECT name, status AS outcome, value, attributes, timestamp
FROM outcomes
WHERE execution_id = ? AND name NOT IN ('execution.completed', 'product_goal')
ORDER BY CASE WHEN name = 'application_outcome' THEN 0 ELSE 1 END, timestamp DESC
LIMIT 1
