WITH signup_days AS (
    SELECT id AS customer_id, strftime(signup_ts, '%Y-%m-%d') AS signup_date
    FROM customers
)
SELECT signup_date FROM signup_days
WHERE signup_date BETWEEN '2024-01-01' AND '2024-12-31';
