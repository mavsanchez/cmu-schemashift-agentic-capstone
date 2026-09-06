WITH emails AS (
    SELECT customer_id, primary_email FROM customer
)
SELECT customer_id, primary_email
FROM emails WHERE primary_email LIKE '%@example.test';
