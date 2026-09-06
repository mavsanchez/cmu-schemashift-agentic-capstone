SELECT COUNT(e.contact_value) AS email_count
FROM customers AS c
LEFT JOIN customer_contacts AS e
  ON e.customer_id = c.id AND e.contact_type = 'email' AND e.is_primary;
