SELECT c.id AS customer_id, c.display_name AS full_name
FROM customers AS c
JOIN sales_orders AS o ON o.customer_id = c.id;
