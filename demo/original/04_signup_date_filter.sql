/*
SchemaShift demo 04: Text date to timestamp.
Source schema: customer_v1. Synthetic retail data only.
Include the boundary date and preserve signup_date as YYYY-MM-DD text.
*/
SELECT customer_id, signup_date
FROM customer
WHERE signup_date >= '2024-01-01';
