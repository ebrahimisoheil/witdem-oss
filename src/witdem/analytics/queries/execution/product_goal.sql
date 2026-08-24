SELECT name, status AS outcome, value, attributes, timestamp
FROM outcomes
WHERE execution_id = ? AND name = 'product_goal'
ORDER BY timestamp DESC
LIMIT 1
