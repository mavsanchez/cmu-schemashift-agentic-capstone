/*
SchemaShift demo 16: Human sign-off on conflicting Pending status mappings.
Source schema: customer_v1. Synthetic retail data only. Valid source query.
Review question: use status code P or the superseded code I for Pending?
Evidence: status_migration.md and status_conflict.md.
Legacy result: customer_id 3. Code I would select customer_id 2 instead.
Copy the human-review request and this file's review question from README.md.
Review is conditional on model flags and retrieved evidence, not the filename.
*/
SELECT customer_id, customer_status
FROM customer
WHERE customer_status = 'Pending';
