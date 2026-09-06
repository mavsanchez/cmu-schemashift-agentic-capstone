SELECT c.id AS customer_id
FROM customers AS c
JOIN customer_profile AS p ON p.customer_id = c.id
JOIN regions AS r ON r.region_id = p.region_id
WHERE r.region_name = 'West';
