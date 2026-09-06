CREATE TABLE customer (
    customer_id INTEGER PRIMARY KEY,
    full_name VARCHAR NOT NULL,
    customer_status VARCHAR,
    region VARCHAR,
    signup_date VARCHAR NOT NULL,
    credit_limit_cents BIGINT,
    segment VARCHAR,
    primary_email VARCHAR,
    customer_active BOOLEAN NOT NULL,
    account_state VARCHAR NOT NULL,
    close_date VARCHAR
);

CREATE TABLE orders (
    order_id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL,
    order_date VARCHAR NOT NULL,
    total_cents BIGINT NOT NULL,
    state VARCHAR NOT NULL
);

CREATE TABLE product (
    product_id INTEGER PRIMARY KEY,
    product_name VARCHAR NOT NULL,
    category VARCHAR NOT NULL,
    price_cents BIGINT NOT NULL
);
