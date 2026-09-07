/*
SchemaShift demo 23: Recall durable memory in a later application session.
Source schema: customer_v1. Synthetic retail data only. Valid source query.
After demos 21 and 22, restart the APP, keep Redis/PostgreSQL running, and reopen
the SAME conversation using Conversation history. Then upload this file.
Migration request:
Migrate this aggregated retail order export using the preferences already saved for this conversation.
Inspect Memory > Learned memory for the original preference and source run ID.
A correct query alone is not proof of recall; inspect the persisted record.
Legacy result: 6 customer totals. Do not start a new conversation.
*/
SELECT customer_id, SUM(total_cents) AS total_cents
FROM orders
GROUP BY customer_id;
