---
schema_version: all
topic: sql-standards
effective_date: 2026-01-01
---
# SchemaShift SQL standards

Migration candidates are one read-only query. Preserve output column names,
order, types, row multiplicity, NULL behavior, filters, grouping, and requested
ordering. Use explicit aliases and qualified join keys. Do not emit DDL, DML,
COPY, ATTACH, PRAGMA, extension-loading statements, or external file readers.
