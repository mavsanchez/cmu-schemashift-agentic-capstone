---
schema_version: customer_v2
old_table: customer
new_table: customer_profile
topic: moved-attributes
effective_date: 2026-01-01
---
# Customer attributes and contacts

Region moved from `customer.region` to `customer_profile.region_id`, joined to
`regions.region_id`; return `regions.region_name` as `region`. Segment moved to
`customer_profile.segment_name`; the sentinel `UNKNOWN` represents the former
SQL NULL and must be converted with `NULLIF(segment_name, 'UNKNOWN')`.

The former `primary_email` is now in `customer_contacts`. Join on customer ID
and filter `contact_type = 'email' AND is_primary = TRUE`. Omitting the primary
filter can duplicate customer rows.

The former `customer_active` flag is derived as
`customers.account_state = 'OPEN' AND customers.closed_at IS NULL`.
