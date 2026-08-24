SELECT execution_id, name, status, value, attributes, timestamp
FROM outcomes
WHERE name = 'product_goal'
ORDER BY timestamp DESC
