SELECT NULLIF(segment_name, 'UNKNOWN') AS segment, COUNT(*) AS customer_count
FROM customer_profile
GROUP BY NULLIF(segment_name, 'UNKNOWN');
