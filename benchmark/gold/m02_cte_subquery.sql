WITH named_customers AS (
    SELECT id AS customer_id, display_name AS full_name FROM customers
)
SELECT customer_id, full_name FROM named_customers WHERE customer_id <= 3;
