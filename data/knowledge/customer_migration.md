---
schema_version: customer_v2
old_table: customer
new_table: customers
topic: customer-core
effective_date: 2026-01-01
---
# Customer core migration

`customer` was renamed to `customers`. Map `customer.customer_id` to
`customers.id` and preserve the outward alias `customer_id`. Map `full_name`
to `display_name` and preserve the outward alias `full_name` for existing
consumers. `signup_date` became the timestamp `signup_ts`; format it as
`YYYY-MM-DD` when the old query returned text.

`credit_limit_cents` became decimal dollars in `credit_limit`. Multiply by 100
and cast to `BIGINT` when preserving the former cents output contract.
