WITH segments AS (
    SELECT customer_id, segment FROM customer
)
SELECT customer_id, segment FROM segments
WHERE segment IS NULL OR segment = 'premium';
