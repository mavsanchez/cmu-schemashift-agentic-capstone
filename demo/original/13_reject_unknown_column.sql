/*
SchemaShift demo 13: Reject a source column absent from the registered schema.
Source schema: customer_v1. Synthetic retail data only.
Intentionally invalid source reference: customer.loyalty_points does not exist.
Expected: original-query execution fails; equivalence cannot be established.
If Human Decision appears after revisions, approval is unavailable; reject.
Use the rejection request in README.md. Do not invent a replacement column.
*/
SELECT customer_id, loyalty_points
FROM customer;
