/*
SchemaShift demo 14: Reject a source table absent from the registered schema.
Source schema: customer_v1. Synthetic retail data only.
Intentionally invalid source reference: archived_orders is not in the demo data.
Expected: original-query execution fails; equivalence cannot be established.
If Human Decision appears after revisions, approval is unavailable; reject.
Use the rejection request in README.md. Do not substitute the orders table.
*/
SELECT order_id, customer_id, total_cents
FROM archived_orders;
