WITH westerners AS (
    SELECT customer_id, region FROM customer WHERE region = 'West'
)
SELECT customer_id, region FROM westerners;
