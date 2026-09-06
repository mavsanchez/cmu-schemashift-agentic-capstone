WITH recent_orders AS (
    SELECT customer_id FROM sales_orders WHERE id >= 104
)
SELECT customer_id FROM recent_orders;
