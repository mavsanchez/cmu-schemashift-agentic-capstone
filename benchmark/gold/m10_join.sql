SELECT o.id AS order_id, s.status_description AS customer_status
FROM sales_orders AS o
JOIN customers AS c ON c.id = o.customer_id
JOIN customer_status AS s ON s.status_code = c.status_code
WHERE s.status_description = 'Pending';
