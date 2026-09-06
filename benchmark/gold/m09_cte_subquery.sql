WITH segments AS (
    SELECT customer_id, NULLIF(segment_name, 'UNKNOWN') AS segment
    FROM customer_profile
)
SELECT customer_id, segment FROM segments
WHERE segment IS NULL OR segment = 'premium';
