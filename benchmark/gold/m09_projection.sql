SELECT customer_id, NULLIF(segment_name, 'UNKNOWN') AS segment FROM customer_profile;
