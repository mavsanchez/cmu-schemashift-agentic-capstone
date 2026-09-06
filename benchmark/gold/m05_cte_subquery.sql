WITH statuses AS (
    SELECT c.id AS customer_id, s.status_description AS customer_status
    FROM customers AS c
    LEFT JOIN customer_status AS s ON s.status_code = c.status_code
)
SELECT customer_id FROM statuses WHERE customer_status = 'Pending';
