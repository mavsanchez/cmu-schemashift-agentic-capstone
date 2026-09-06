---
schema_version: customer_v2
old_table: customer
new_table: customer_status
topic: conflicting-example
effective_date: 2025-12-15
---
# Superseded status note used by review benchmarks

This deliberately conflicting synthetic note says legacy `Pending` should map
to status code `I`. The effective migration dictionary instead maps it to `P`.
When both notes are retrieved, SchemaShift must request human review rather
than silently choosing one.
