-- ======================================================
-- Federated Analytics Platform — Metadata Database Schema
-- This database is internal to the platform.
-- It stores: dataset registry, column metadata, audit logs.
-- The AI Engine reads from this to build LLM prompts.
-- ======================================================

-- ──────────────────────────────────────────────────────
-- Dataset Registry
-- Catalogs all known data sources and their Trino mapping
-- ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS datasets (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(255) NOT NULL UNIQUE,
    description     TEXT,
    source_type     VARCHAR(50) NOT NULL CHECK (source_type IN ('postgresql', 'mongodb', 'trino')),
    trino_catalog   VARCHAR(100) NOT NULL,  -- e.g., "postgres_source"
    trino_schema    VARCHAR(100) NOT NULL,  -- e.g., "public" or "default"
    trino_table     VARCHAR(100) NOT NULL,  -- e.g., "orders"
    is_active       BOOLEAN DEFAULT true,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ──────────────────────────────────────────────────────
-- Column Metadata
-- Describes columns for each dataset — fed into AI prompts
-- ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dataset_columns (
    id              SERIAL PRIMARY KEY,
    dataset_id      INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    column_name     VARCHAR(255) NOT NULL,
    data_type       VARCHAR(100) NOT NULL,   -- e.g., "VARCHAR", "INTEGER", "TIMESTAMP"
    description     TEXT,                    -- Human-readable description for LLM context
    is_primary_key  BOOLEAN DEFAULT false,
    is_joinable     BOOLEAN DEFAULT false,   -- Marks columns usable as join keys
    sample_values   TEXT,                    -- Comma-separated examples for the LLM
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ──────────────────────────────────────────────────────
-- Audit Log
-- Every query request is logged here by the Core API
-- ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_logs (
    id              SERIAL PRIMARY KEY,
    request_id      UUID NOT NULL DEFAULT gen_random_uuid(),
    user_token      VARCHAR(255),            -- Hashed/truncated token identifier
    question        TEXT NOT NULL,           -- Original NL question or raw SQL
    mode            VARCHAR(10) NOT NULL CHECK (mode IN ('ai', 'sql')),
    query_plan      JSONB,                   -- The generated/used query plan
    sql_executed    TEXT,                    -- Final SQL executed against Trino
    status          VARCHAR(20) NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'success', 'error')),
    error_message   TEXT,
    row_count       INTEGER,
    duration_ms     INTEGER,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ──────────────────────────────────────────────────────
-- Indexes
-- ──────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_status ON audit_logs(status);
CREATE INDEX IF NOT EXISTS idx_dataset_columns_dataset_id ON dataset_columns(dataset_id);

-- ──────────────────────────────────────────────────────
-- Seed: Example Dataset Registry Entries
-- Users should UPDATE these (or INSERT their own) after
-- creating their actual tables in postgres-source and MongoDB.
--
-- The AI Engine reads these rows to build its LLM prompts.
-- The more accurate the metadata, the better the AI output.
-- ──────────────────────────────────────────────────────

INSERT INTO datasets (name, description, source_type, trino_catalog, trino_schema, trino_table)
VALUES
    (
        'orders',
        'Customer order transactions from the PostgreSQL source database. Use this for revenue, order counts, and customer analysis.',
        'postgresql',
        'postgres_source',
        'public',
        'orders'
    ),
    (
        'products',
        'Product catalog from MongoDB. Contains product details, categories, pricing, and inventory.',
        'mongodb',
        'mongodb',
        'default',
        'products'
    )
ON CONFLICT (name) DO NOTHING;

-- Seed: Example column metadata for 'orders' dataset
INSERT INTO dataset_columns (dataset_id, column_name, data_type, description, is_primary_key, is_joinable, sample_values)
SELECT
    d.id,
    col.column_name,
    col.data_type,
    col.description,
    col.is_primary_key,
    col.is_joinable,
    col.sample_values
FROM datasets d
CROSS JOIN (VALUES
    ('order_id',       'INTEGER',   'Unique order identifier',                                  true,  false, '1001, 1002, 1003'),
    ('customer_name',  'VARCHAR',   'Full name of the customer who placed the order',            false, false, 'Alice Smith, Bob Jones'),
    ('product',        'VARCHAR',   'Name of the product ordered — matches products.name in MongoDB', false, true,  'Widget A, Gadget B'),
    ('amount',         'DECIMAL',   'Total order amount in USD',                                 false, false, '49.99, 129.00, 299.50'),
    ('order_date',     'DATE',      'Date the order was placed',                                 false, false, '2024-01-15, 2024-03-22'),
    ('region',         'VARCHAR',   'Geographic region of the order',                            false, false, 'North, South, East, West')
) AS col(column_name, data_type, description, is_primary_key, is_joinable, sample_values)
WHERE d.name = 'orders'
ON CONFLICT DO NOTHING;

-- Seed: Example column metadata for 'products' dataset
INSERT INTO dataset_columns (dataset_id, column_name, data_type, description, is_primary_key, is_joinable, sample_values)
SELECT
    d.id,
    col.column_name,
    col.data_type,
    col.description,
    col.is_primary_key,
    col.is_joinable,
    col.sample_values
FROM datasets d
CROSS JOIN (VALUES
    ('product_id',   'VARCHAR',  'Unique product identifier (MongoDB _id or custom)',         true,  false, 'PROD-001, PROD-002'),
    ('name',         'VARCHAR',  'Product name — matches orders.product in PostgreSQL',       false, true,  'Widget A, Gadget B'),
    ('category',     'VARCHAR',  'Product category for grouping and filtering',               false, false, 'Electronics, Accessories, Apparel'),
    ('price',        'DOUBLE',   'Unit price of the product in USD',                          false, false, '29.99, 99.99, 249.00'),
    ('supplier',     'VARCHAR',  'Name of the supplier or manufacturer',                      false, false, 'Acme Corp, TechMakers Inc'),
    ('stock_count',  'INTEGER',  'Current inventory count',                                   false, false, '150, 42, 0')
) AS col(column_name, data_type, description, is_primary_key, is_joinable, sample_values)
WHERE d.name = 'products'
ON CONFLICT DO NOTHING;

-- ──────────────────────────────────────────────────────
-- Update trigger for datasets.updated_at
-- ──────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS update_datasets_updated_at ON datasets;
CREATE TRIGGER update_datasets_updated_at
    BEFORE UPDATE ON datasets
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
