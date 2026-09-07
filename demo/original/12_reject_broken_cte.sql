/*
SchemaShift demo 12: Reject an unfinished common table expression.
Source schema: customer_v1. Synthetic retail data only.
Intentionally invalid SQL: the CTE has no closing parenthesis or outer query.
Expected: SQL preflight stops; no migrated artifact.
Use the rejection request in README.md. Do not repair this file before the demo.
*/
WITH active_customers AS (
    SELECT customer_id
    FROM customer
    WHERE customer_active = TRUE;
