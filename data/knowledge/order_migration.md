---
schema_version: customer_v2
old_table: orders
new_table: sales_orders
topic: orders
effective_date: 2026-01-01
---
# Order migration

`orders` became `sales_orders`. Map `order_id` to `id`, `order_date` to
`ordered_at`, and `total_cents` to `total_amount`. Decimal dollars must be
multiplied by 100 and cast to `BIGINT` when an existing query returns cents.
`state` maps through `order_states.order_state_code = order_states.state_code`
when a descriptive state is required.
