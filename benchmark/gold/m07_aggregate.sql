SELECT (account_state = 'OPEN') AS customer_active, COUNT(*) AS customer_count
FROM customers
GROUP BY (account_state = 'OPEN');
