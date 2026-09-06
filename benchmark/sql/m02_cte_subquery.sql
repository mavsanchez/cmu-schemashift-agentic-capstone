WITH named_customers AS (
    SELECT customer_id, full_name FROM customer
)
SELECT customer_id, full_name FROM named_customers WHERE customer_id <= 3;
