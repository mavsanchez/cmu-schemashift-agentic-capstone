SELECT customer_id, COUNT(*) AS order_count FROM sales_orders GROUP BY customer_id;
