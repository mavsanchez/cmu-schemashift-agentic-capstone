SELECT c.customer_id, o.order_date
FROM customer AS c
JOIN orders AS o ON o.customer_id = c.customer_id
WHERE o.order_date >= c.signup_date;
