SELECT customer_id, COUNT(*) AS order_count FROM orders GROUP BY customer_id;
