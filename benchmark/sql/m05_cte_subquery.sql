WITH statuses AS (
    SELECT customer_id, customer_status FROM customer
)
SELECT customer_id FROM statuses WHERE customer_status = 'Pending';
