/*
SchemaShift demo 19: Human sign-off on an unmatched order-state code.
Source schema: customer_v1. Synthetic retail data only. Valid source query.
Review question: how should code X retain the legacy Manual description?
Evidence: order_migration.md plus paired source/target data and validation output.
The new lookup has no X row. An inner join drops legacy order_id 109.
Copy the human-review request and this file's review question from README.md.
Review is conditional on model flags and retrieved evidence, not the filename.
*/
SELECT order_id, state
FROM orders
WHERE state = 'Manual';
