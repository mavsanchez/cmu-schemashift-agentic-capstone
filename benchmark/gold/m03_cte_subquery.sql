WITH order_values AS (
    SELECT id AS order_id, CAST(total_amount * 100 AS BIGINT) AS total_cents
    FROM sales_orders
)
SELECT order_id, total_cents FROM order_values
WHERE total_cents BETWEEN 2500 AND 50000;
