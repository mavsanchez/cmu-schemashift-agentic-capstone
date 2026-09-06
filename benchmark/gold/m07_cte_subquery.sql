WITH flags AS (
    SELECT id AS customer_id, (account_state = 'OPEN') AS customer_active
    FROM customers
)
SELECT customer_id FROM flags WHERE customer_active = FALSE;
