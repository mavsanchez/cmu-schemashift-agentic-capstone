/*
SchemaShift demo 08: One-to-many contact join.
Source schema: customer_v1. Synthetic retail data only.
Return one row per order. Preserve null emails and avoid duplicate orders.
*/
SELECT o.order_id, c.primary_email
FROM orders AS o
JOIN customer AS c
    ON c.customer_id = o.customer_id;
