SELECT c.id AS customer_id, strftime(o.ordered_at, '%Y-%m-%d') AS order_date
FROM customers AS c
JOIN sales_orders AS o ON o.customer_id = c.id
WHERE o.ordered_at >= c.signup_ts;
