SELECT c.id AS customer_id
FROM customers AS c
JOIN customer_contacts AS e
  ON e.customer_id = c.id AND e.contact_type = 'email' AND e.is_primary
WHERE e.contact_value IS NOT NULL;
