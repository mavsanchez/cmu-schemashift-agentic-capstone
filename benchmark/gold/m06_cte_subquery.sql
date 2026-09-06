WITH westerners AS (
    SELECT c.id AS customer_id, r.region_name AS region
    FROM customers AS c
    JOIN customer_profile AS p ON p.customer_id = c.id
    JOIN regions AS r ON r.region_id = p.region_id
    WHERE r.region_name = 'West'
)
SELECT customer_id, region FROM westerners;
