/*
SchemaShift demo 05: Status moved into a lookup table.
Source schema: customer_v1. Synthetic retail data only.
Select customers with the descriptive status Active.
*/
SELECT customer_id
FROM customer
WHERE customer_status = 'Active';
