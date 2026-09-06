/*
SchemaShift demo 01: Table rename.
Source schema: customer_v1. Synthetic retail data only.
Return one customer_id per order, preserving repeated customer IDs.
*/
SELECT customer_id
FROM orders;
