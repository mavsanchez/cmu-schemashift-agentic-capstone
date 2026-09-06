WITH pending_customers AS (
    SELECT c.id AS customer_id
    FROM customers AS c
    JOIN customer_status AS s ON s.status_code = c.status_code
    WHERE s.status_description = 'Pending'
)
SELECT customer_id FROM pending_customers;
