---
schema_version: customer_v2
old_table: customer
new_table: customer_profile
topic: null-default-semantics
effective_date: 2026-01-01
---
# Profile default semantics

During migration, a null legacy `customer.segment` was loaded into
`customer_profile.segment_name` as the sentinel `UNKNOWN`. Existing query
contracts that returned null must reverse that load-time default with
`NULLIF(customer_profile.segment_name, 'UNKNOWN')`. Predicates for legacy null
segments must match the sentinel explicitly.
