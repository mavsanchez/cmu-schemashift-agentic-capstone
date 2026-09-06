WITH order_values AS (
    SELECT order_id, total_cents FROM orders
)
SELECT order_id, total_cents FROM order_values
WHERE total_cents BETWEEN 2500 AND 50000;
