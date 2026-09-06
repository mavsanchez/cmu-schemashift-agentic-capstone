SELECT r.region_name AS region, COUNT(*) AS customer_count
FROM customers AS c
LEFT JOIN customer_profile AS p ON p.customer_id = c.id
LEFT JOIN regions AS r ON r.region_id = p.region_id
GROUP BY r.region_name;
