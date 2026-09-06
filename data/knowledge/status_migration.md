---
schema_version: customer_v2
old_table: customer
new_table: customer_status
topic: customer-status
effective_date: 2026-01-01
---
# Customer status migration

`customer.customer_status` moved to a normalized lookup. Join
`customers.status_code` to `customer_status.status_code` and return
`customer_status.status_description` when the legacy query returned the
descriptive value. Use a left join so a null legacy status remains null.

The authoritative synthetic mapping is `Active` to `A`, `Inactive` to `I`,
and `Pending` to `P`.
