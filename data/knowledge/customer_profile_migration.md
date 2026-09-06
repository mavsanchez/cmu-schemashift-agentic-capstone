---
schema_version: customer_v2
old_table: customer
new_table: customer_profile
topic: customer-profile
effective_date: 2026-01-01
---
# Customer profile migration

The legacy `customer.region` attribute moved through
`customer_profile.region_id` to `regions.region_id`. Return
`regions.region_name` with the legacy alias `region`. Both joins must be left
joins when the old query retained customers whose region was null.

The legacy `customer.segment` attribute moved to
`customer_profile.segment_name`. The migration loader represents a missing
legacy segment with the sentinel `UNKNOWN`; use
`NULLIF(segment_name, 'UNKNOWN')` when preserving the old nullable contract.
