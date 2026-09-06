SELECT o.order_id, c.customer_status
FROM orders AS o
JOIN customer AS c ON c.customer_id = o.customer_id;
