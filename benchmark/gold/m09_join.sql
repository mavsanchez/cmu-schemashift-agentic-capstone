SELECT o.id AS order_id, NULLIF(p.segment_name, 'UNKNOWN') AS segment
FROM sales_orders AS o
JOIN customer_profile AS p ON p.customer_id = o.customer_id;
