SELECT id AS customer_id, strftime(signup_ts, '%Y-%m-%d') AS signup_date FROM customers WHERE signup_ts >= TIMESTAMP '2024-01-01';
