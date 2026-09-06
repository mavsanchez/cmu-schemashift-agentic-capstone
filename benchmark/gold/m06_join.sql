SELECT o.id AS order_id, r.region_name AS region
FROM sales_orders AS o
JOIN customers AS c ON c.id = o.customer_id
LEFT JOIN customer_profile AS p ON p.customer_id = c.id
LEFT JOIN regions AS r ON r.region_id = p.region_id;
