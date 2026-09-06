SELECT o.id AS order_id, e.contact_value AS primary_email
FROM sales_orders AS o
JOIN customers AS c ON c.id = o.customer_id
LEFT JOIN customer_contacts AS e
  ON e.customer_id = c.id AND e.contact_type = 'email' AND e.is_primary;
