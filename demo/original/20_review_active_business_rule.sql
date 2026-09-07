/*
SchemaShift demo 20: Human sign-off on the meaning of customer_active.
Source schema: customer_v1. Synthetic retail data only. Valid source query.
Review question: derive activity from account_state or the status lookup's flag?
Evidence: customer_active_rule.md. The documented rule is account_state = OPEN.
Legacy inactive customers: 2, 3, and 5. Matching this fixture alone is not policy approval.
Copy the human-review request and this file's review question from README.md.
Review is conditional on model flags and retrieved evidence, not the filename.
*/
SELECT customer_id, account_state, customer_active
FROM customer
WHERE customer_active = FALSE;
