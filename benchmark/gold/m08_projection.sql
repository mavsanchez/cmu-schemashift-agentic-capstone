SELECT c.id AS customer_id, e.contact_value AS primary_email
FROM customers AS c
LEFT JOIN customer_contacts AS e
  ON e.customer_id = c.id AND e.contact_type = 'email' AND e.is_primary;
