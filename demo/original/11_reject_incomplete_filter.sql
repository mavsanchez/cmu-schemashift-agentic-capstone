/*
SchemaShift demo 11: Reject an incomplete source filter.
Source schema: customer_v1. Synthetic retail data only.
Intentionally invalid SQL: the comparison has no right-hand value.
Expected: SQL preflight stops; no migrated artifact.
Use the rejection request in README.md. Do not repair this file before the demo.
*/
SELECT order_id, total_cents
FROM orders
WHERE total_cents >;
