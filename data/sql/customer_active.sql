SELECT customer_id, full_name, primary_email
FROM customer
WHERE customer_active = TRUE
ORDER BY customer_id;
