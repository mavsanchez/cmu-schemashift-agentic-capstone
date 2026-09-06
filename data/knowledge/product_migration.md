---
schema_version: customer_v2
old_table: product
new_table: catalog_products
topic: products
effective_date: 2026-01-01
---
# Product migration

Map `product.product_id` to `catalog_products.id`, `product_name` to `name`,
`category` to `category_name`, and integer `price_cents` to decimal
`unit_price`. Preserve legacy column aliases and convert dollars back to cents
when the original output contract requires it.
