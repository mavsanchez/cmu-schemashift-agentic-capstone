/*
SchemaShift demo 22: Recall the preference saved by demo 21.
Source schema: customer_v1. Synthetic retail data only. Valid source query.
Run in the SAME conversation as demo 21, after its memory write is confirmed.
Migration request:
Migrate this filtered retail order export using the preferences already saved for this conversation.
Memory > Learned memory must show the saved preference with its original provenance.
Do not prepend Remember: or repeat the preference; this run demonstrates recall.
Legacy result: 6 orders; customer IDs 1 and 4 each occur twice.
*/
SELECT order_id, customer_id, total_cents
FROM orders
WHERE total_cents >= 2500;
