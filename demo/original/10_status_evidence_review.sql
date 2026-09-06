/*
SchemaShift demo 10: Conflicting migration evidence.
Source schema: customer_v1. Synthetic retail data only.
Preserve descriptive statuses, including Pending and null values.
The bundled status migration documents include conflicting status guidance.
*/
SELECT customer_id, customer_status
FROM customer;
