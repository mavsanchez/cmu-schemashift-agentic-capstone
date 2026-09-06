/*
SchemaShift demo 07: Derived business-rule mapping inside a CTE.
Source schema: customer_v1. Synthetic retail data only.
Preserve the meaning of customer_active, including closed accounts.
*/
WITH flags AS (
    SELECT customer_id, customer_active
    FROM customer
)
SELECT customer_id
FROM flags
WHERE customer_active = FALSE;
