WITH signup_days AS (
    SELECT customer_id, signup_date FROM customer
)
SELECT signup_date FROM signup_days
WHERE signup_date BETWEEN '2024-01-01' AND '2024-12-31';
