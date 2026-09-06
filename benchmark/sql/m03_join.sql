SELECT c.customer_id, o.total_cents
FROM customer AS c
JOIN orders AS o ON o.customer_id = c.customer_id;
