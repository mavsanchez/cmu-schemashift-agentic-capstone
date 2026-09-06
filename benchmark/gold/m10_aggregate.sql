SELECT s.status_description AS customer_status, COUNT(*) AS customer_count
FROM customers AS c
LEFT JOIN customer_status AS s ON s.status_code = c.status_code
GROUP BY s.status_description;
