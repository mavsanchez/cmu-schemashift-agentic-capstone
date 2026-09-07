/*
SchemaShift demo 21: Persist a durable order-export preference.
Source schema: customer_v1. Synthetic retail data only. Valid source query.
Start ONE new conversation for demos 21, 22, and 23; retain it throughout.
Migration request (copy exactly, including the initial Remember:):
Remember: Retail order exports retain repeated customer IDs and integer cents under the legacy total_cents column name.
After successful completion, Memory > Learned memory > Memory write decisions
must show stored=true for this user_preference. SQL comments alone do not store it.
*/
SELECT order_id, customer_id, total_cents
FROM orders;
