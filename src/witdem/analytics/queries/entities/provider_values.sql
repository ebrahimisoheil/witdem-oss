SELECT DISTINCT json_extract_string(attributes, '$.provider') AS value
FROM operations
WHERE json_extract_string(attributes, '$.provider') IS NOT NULL
ORDER BY value
