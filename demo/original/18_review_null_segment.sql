/*
SchemaShift demo 18: Human sign-off on null versus default-label semantics.
Source schema: customer_v1. Synthetic retail data only. Valid source query.
Review question: preserve legacy null segments, or expose the new UNKNOWN label?
Evidence: profile_defaults.md and customer_profile_migration.md.
Legacy result: customer_id 3 and 6, both with a null segment.
Copy the human-review request and this file's review question from README.md.
Review is conditional on model flags and retrieved evidence, not the filename.
*/
SELECT customer_id, segment
FROM customer
WHERE segment IS NULL;
