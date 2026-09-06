WITH pending_customers AS (
    SELECT customer_id FROM customer WHERE customer_status = 'Pending'
)
SELECT customer_id FROM pending_customers;
