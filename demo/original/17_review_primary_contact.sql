/*
SchemaShift demo 17: Human sign-off on the one-to-many contact join.
Source schema: customer_v1. Synthetic retail data only. Valid source query.
Review question: primary email only, or any contact marked as primary?
Evidence: contact_migration.md.
Legacy result: 6 customers, with one null email. Dan has extra new-schema contacts.
Copy the human-review request and this file's review question from README.md.
Review is conditional on model flags and retrieved evidence, not the filename.
*/
SELECT customer_id, full_name, primary_email
FROM customer;
