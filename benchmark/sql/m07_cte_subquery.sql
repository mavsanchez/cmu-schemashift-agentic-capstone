WITH flags AS (
    SELECT customer_id, customer_active FROM customer
)
SELECT customer_id FROM flags WHERE customer_active = FALSE;
