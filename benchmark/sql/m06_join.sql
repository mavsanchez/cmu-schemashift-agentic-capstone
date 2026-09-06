SELECT o.order_id, c.region
FROM orders AS o
JOIN customer AS c ON c.customer_id = o.customer_id;
