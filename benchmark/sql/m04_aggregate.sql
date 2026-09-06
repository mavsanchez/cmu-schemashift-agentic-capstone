SELECT account_state, MIN(signup_date) AS first_signup_date FROM customer GROUP BY account_state;
