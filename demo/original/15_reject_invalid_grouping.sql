/*
SchemaShift demo 15: Reject an undefined aggregation contract.
Source schema: customer_v1. Synthetic retail data only.
Intentionally invalid SQL: customer_id is neither aggregated nor grouped.
Expected: original-query execution fails; equivalence cannot be established.
If Human Decision appears after revisions, approval is unavailable; reject.
Use the rejection request in README.md. Do not guess the missing grouping.
*/
SELECT customer_id, SUM(total_cents) AS total_cents
FROM orders;
