SELECT o.customer_id
FROM sales_orders AS o
JOIN customers AS c ON c.id = o.customer_id
WHERE c.account_state = 'OPEN';
