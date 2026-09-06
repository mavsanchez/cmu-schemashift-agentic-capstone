/*
SchemaShift demo 06: Attribute moved into other tables.
Source schema: customer_v1. Synthetic retail data only.
Count customers by region, retaining the group for a null region.
*/
SELECT
    region,
    COUNT(*) AS customer_count
FROM customer
GROUP BY region;
