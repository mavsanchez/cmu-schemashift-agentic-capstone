SELECT o.customer_id
FROM orders AS o
JOIN customer AS c ON c.customer_id = o.customer_id
WHERE c.account_state = 'OPEN';
