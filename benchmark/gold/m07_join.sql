SELECT o.id AS order_id, (c.account_state = 'OPEN') AS customer_active
FROM sales_orders AS o
JOIN customers AS c ON c.id = o.customer_id;
