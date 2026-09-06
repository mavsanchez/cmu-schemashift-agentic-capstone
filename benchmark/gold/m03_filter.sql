SELECT id AS order_id, CAST(total_amount * 100 AS BIGINT) AS total_cents FROM sales_orders WHERE total_amount >= 100.00;
