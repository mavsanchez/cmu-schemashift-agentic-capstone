SELECT customer_id, SUM(total_cents) AS total_cents FROM orders GROUP BY customer_id;
