/*
SchemaShift demo 09: Null and default semantics.
Source schema: customer_v1. Synthetic retail data only.
Preserve the null segment group and the customer counts for every segment.
*/
SELECT
    segment,
    COUNT(*) AS customer_count
FROM customer
GROUP BY segment;
