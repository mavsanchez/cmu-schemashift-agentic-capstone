SELECT c.id AS customer_id, CAST(o.total_amount * 100 AS BIGINT) AS total_cents
FROM customers AS c
JOIN sales_orders AS o ON o.customer_id = c.id;
