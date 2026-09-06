SELECT c.customer_id, c.full_name
FROM customer AS c
JOIN orders AS o ON o.customer_id = c.customer_id;
