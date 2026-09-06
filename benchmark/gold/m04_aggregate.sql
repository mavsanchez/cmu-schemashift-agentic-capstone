SELECT account_state, strftime(MIN(signup_ts), '%Y-%m-%d') AS first_signup_date FROM customers GROUP BY account_state;
