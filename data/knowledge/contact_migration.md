---
schema_version: customer_v2
old_table: customer
new_table: customer_contacts
topic: customer-contacts
effective_date: 2026-01-01
---
# Primary contact migration

`customer.primary_email` moved to the one-to-many `customer_contacts` table.
Join on `customer_id` and constrain the joined row to
`contact_type = 'email' AND is_primary`. A left join preserves customers with
no email. Omitting either discriminator can duplicate customer and order rows.
