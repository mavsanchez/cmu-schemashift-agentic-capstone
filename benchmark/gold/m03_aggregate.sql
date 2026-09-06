SELECT customer_id, SUM(CAST(total_amount * 100 AS BIGINT)) AS total_cents FROM sales_orders GROUP BY customer_id;
