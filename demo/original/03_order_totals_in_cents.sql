/*
SchemaShift demo 03: Integer cents to decimal currency.
Source schema: customer_v1. Synthetic retail data only.
Keep each customer's aggregated total in cents with exact numeric values.
*/
SELECT
    customer_id,
    SUM(total_cents) AS total_cents
FROM orders
GROUP BY customer_id;
