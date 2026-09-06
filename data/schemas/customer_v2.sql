CREATE TABLE customers (
    id INTEGER PRIMARY KEY,
    display_name VARCHAR NOT NULL,
    status_code VARCHAR,
    signup_ts TIMESTAMP NOT NULL,
    credit_limit DECIMAL(18, 2),
    account_state VARCHAR NOT NULL,
    closed_at TIMESTAMP
);

CREATE TABLE customer_status (
    status_code VARCHAR PRIMARY KEY,
    status_description VARCHAR NOT NULL,
    is_active BOOLEAN NOT NULL
);

CREATE TABLE customer_profile (
    customer_id INTEGER PRIMARY KEY,
    region_id INTEGER,
    segment_name VARCHAR NOT NULL
);

CREATE TABLE regions (
    region_id INTEGER PRIMARY KEY,
    region_name VARCHAR NOT NULL
);

CREATE TABLE customer_contacts (
    contact_id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL,
    contact_type VARCHAR NOT NULL,
    contact_value VARCHAR NOT NULL,
    is_primary BOOLEAN NOT NULL
);

CREATE TABLE sales_orders (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL,
    ordered_at TIMESTAMP NOT NULL,
    total_amount DECIMAL(18, 2) NOT NULL,
    order_state_code VARCHAR NOT NULL
);

CREATE TABLE order_states (
    state_code VARCHAR PRIMARY KEY,
    state_description VARCHAR NOT NULL
);

CREATE TABLE catalog_products (
    id INTEGER PRIMARY KEY,
    name VARCHAR NOT NULL,
    category_name VARCHAR NOT NULL,
    unit_price DECIMAL(18, 2) NOT NULL
);
