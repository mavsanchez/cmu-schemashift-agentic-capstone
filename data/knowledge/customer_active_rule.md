---
schema_version: customer_v2
old_table: customer
new_table: customers
topic: customer-active-rule
effective_date: 2026-01-01
---
# Active-customer business rule

The removed `customer.customer_active` flag is derived from account state in
the new schema. It is true exactly when `customers.account_state = 'OPEN'` and
false for every other non-null account state. Preserve the legacy
`customer_active` output alias.
