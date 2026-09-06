WITH recent_orders AS (
    SELECT customer_id FROM orders WHERE order_id >= 104
)
SELECT customer_id FROM recent_orders;
