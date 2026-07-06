-- ======================================================
-- Federated Analytics Platform — Metadata Database Schema v2
-- 
-- New in v2:
--   - data_sources: registered external connections (Postgres, ES, Mongo, etc.)
--   - table_relationships: cross-source join hints for AI
--   - dashboards + dashboard_widgets: custom dashboard builder
--   - datasets.source_type now allows 'elasticsearch' and 'trino'
-- ======================================================

-- ──────────────────────────────────────────────────────
-- Data Sources Registry (NEW)
-- Registered database/cluster connections managed via UI.
-- Each source maps to a Trino catalog for federated queries.
-- ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS data_sources (
    id                  SERIAL PRIMARY KEY,
    name                VARCHAR(255) NOT NULL UNIQUE,    -- Human-readable name
    source_type         VARCHAR(50) NOT NULL             -- 'postgresql', 'mongodb', 'elasticsearch', 'mysql', etc.
                            CHECK (source_type IN ('postgresql', 'mongodb', 'elasticsearch', 'mysql', 'trino')),
    host                VARCHAR(255) NOT NULL,
    port                INTEGER NOT NULL,
    database_name       VARCHAR(255),                    -- DB name (Postgres) or index (ES)
    username            VARCHAR(255),                    -- Optional auth
    password_encrypted  TEXT,                            -- Stored encrypted (plaintext in POC)
    extra_config        JSONB DEFAULT '{}',              -- Source-specific config (TLS, auth type, etc.)
    trino_catalog       VARCHAR(100) NOT NULL UNIQUE,    -- Trino catalog name for this source
    is_active           BOOLEAN DEFAULT true,
    schema_cache        JSONB DEFAULT '{}',              -- Cached schema/mappings from last refresh
    last_schema_refresh TIMESTAMPTZ,                     -- When schema was last fetched
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

-- ──────────────────────────────────────────────────────
-- Dataset Registry
-- Catalogs known tables/collections with their Trino mapping.
-- Updated: source_type now includes 'elasticsearch'.
-- ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS datasets (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(255) NOT NULL UNIQUE,
    description     TEXT,
    source_type     VARCHAR(50) NOT NULL CHECK (source_type IN ('postgresql', 'mongodb', 'elasticsearch', 'trino')),
    trino_catalog   VARCHAR(100) NOT NULL,
    trino_schema    VARCHAR(100) NOT NULL,
    trino_table     VARCHAR(100) NOT NULL,
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
    data_type       VARCHAR(100) NOT NULL,
    description     TEXT,
    is_primary_key  BOOLEAN DEFAULT false,
    is_joinable     BOOLEAN DEFAULT false,
    sample_values   TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ──────────────────────────────────────────────────────
-- Table Relationships (NEW)
-- Cross-source join hints for the AI planner
-- ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS table_relationships (
    id              SERIAL PRIMARY KEY,
    from_trino_path VARCHAR(500) NOT NULL,      -- e.g., 'postgres_source.public.employees'
    from_column     VARCHAR(255) NOT NULL,
    to_trino_path   VARCHAR(500) NOT NULL,      -- e.g., 'elasticsearch.default.employee_profiles'
    to_column       VARCHAR(255) NOT NULL,
    join_type       VARCHAR(50) DEFAULT 'INNER', -- INNER, LEFT, etc.
    cast_expression VARCHAR(255),               -- e.g., 'CAST(to_col AS INTEGER)'
    description     TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ──────────────────────────────────────────────────────
-- Audit Log
-- Every query request is logged here by the Core API
-- ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_logs (
    id              SERIAL PRIMARY KEY,
    request_id      UUID NOT NULL DEFAULT gen_random_uuid(),
    user_token      VARCHAR(255),
    question        TEXT NOT NULL,
    mode            VARCHAR(10) NOT NULL CHECK (mode IN ('ai', 'sql')),
    query_plan      JSONB,
    sql_executed    TEXT,
    status          VARCHAR(20) NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'success', 'error')),
    error_message   TEXT,
    row_count       INTEGER,
    duration_ms     INTEGER,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ──────────────────────────────────────────────────────
-- Dashboards (NEW)
-- Custom user dashboards with saved query widgets
-- ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dashboards (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(255) NOT NULL,
    description TEXT DEFAULT '',
    layout      JSONB DEFAULT '{}',         -- react-grid-layout config
    is_active   BOOLEAN DEFAULT true,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ──────────────────────────────────────────────────────
-- Dashboard Widgets (NEW)
-- Individual panels in a dashboard, each backed by a saved query
-- ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dashboard_widgets (
    id              SERIAL PRIMARY KEY,
    dashboard_id    INTEGER NOT NULL REFERENCES dashboards(id) ON DELETE CASCADE,
    title           VARCHAR(255) NOT NULL,
    query_sql       TEXT NOT NULL,          -- Trino SQL to power this widget
    chart_type      VARCHAR(50) DEFAULT 'table'
                        CHECK (chart_type IN ('table', 'bar', 'line', 'pie', 'area', 'scatter', 'number', 'gauge')),
    chart_config    JSONB DEFAULT '{}',     -- ECharts or custom config
    grid_position   JSONB DEFAULT '{"x":0,"y":0,"w":6,"h":4}',
    refresh_rate_ms INTEGER DEFAULT 0,      -- 0 = no auto-refresh
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ──────────────────────────────────────────────────────
-- Indexes
-- ──────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_status ON audit_logs(status);
CREATE INDEX IF NOT EXISTS idx_dataset_columns_dataset_id ON dataset_columns(dataset_id);
CREATE INDEX IF NOT EXISTS idx_dashboards_active ON dashboards(is_active);
CREATE INDEX IF NOT EXISTS idx_dashboard_widgets_dashboard ON dashboard_widgets(dashboard_id);
CREATE INDEX IF NOT EXISTS idx_data_sources_active ON data_sources(is_active);

-- ──────────────────────────────────────────────────────
-- Seed: Built-in Data Sources (match existing Trino catalogs)
-- ──────────────────────────────────────────────────────
INSERT INTO data_sources (name, source_type, host, port, database_name, trino_catalog, is_active)
VALUES
    ('PostgreSQL Source', 'postgresql', 'postgres-source', 5432, 'source_db', 'postgres_source', true),
    ('MongoDB Source',    'mongodb',    'mongo-source',    27017, 'employee_db', 'mongodb', true)
ON CONFLICT (name) DO NOTHING;

-- ──────────────────────────────────────────────────────
-- Seed: Example Dataset Registry Entries
-- ──────────────────────────────────────────────────────
INSERT INTO datasets (name, description, source_type, trino_catalog, trino_schema, trino_table)
VALUES
    (
        'orders',
        'Customer order transactions. Use for revenue, order counts, and customer analysis.',
        'postgresql', 'postgres_source', 'public', 'orders'
    ),
    (
        'products',
        'Product catalog from MongoDB. Contains product details, categories, pricing, and inventory.',
        'mongodb', 'mongodb', 'default', 'products'
    )
ON CONFLICT (name) DO NOTHING;

-- Seed: Column metadata for 'orders'
INSERT INTO dataset_columns (dataset_id, column_name, data_type, description, is_primary_key, is_joinable, sample_values)
SELECT d.id, col.column_name, col.data_type, col.description, col.is_primary_key, col.is_joinable, col.sample_values
FROM datasets d
CROSS JOIN (VALUES
    ('order_id',      'INTEGER', 'Unique order identifier',                                   true,  false, '1001, 1002, 1003'),
    ('customer_name', 'VARCHAR', 'Full name of the customer who placed the order',            false, false, 'Alice Smith, Bob Jones'),
    ('product',       'VARCHAR', 'Name of the product ordered — matches products.name in MongoDB', false, true, 'Widget A, Gadget B'),
    ('amount',        'DECIMAL', 'Total order amount in USD',                                  false, false, '49.99, 129.00'),
    ('order_date',    'DATE',    'Date the order was placed',                                  false, false, '2024-01-15'),
    ('region',        'VARCHAR', 'Geographic region of the order',                             false, false, 'North, South, East, West')
) AS col(column_name, data_type, description, is_primary_key, is_joinable, sample_values)
WHERE d.name = 'orders'
ON CONFLICT DO NOTHING;

-- Seed: Column metadata for 'products'
INSERT INTO dataset_columns (dataset_id, column_name, data_type, description, is_primary_key, is_joinable, sample_values)
SELECT d.id, col.column_name, col.data_type, col.description, col.is_primary_key, col.is_joinable, col.sample_values
FROM datasets d
CROSS JOIN (VALUES
    ('product_id',  'VARCHAR', 'Unique product identifier',                            true,  false, 'PROD-001, PROD-002'),
    ('name',        'VARCHAR', 'Product name — matches orders.product in PostgreSQL',  false, true,  'Widget A, Gadget B'),
    ('category',    'VARCHAR', 'Product category',                                     false, false, 'Electronics, Accessories'),
    ('price',       'DOUBLE',  'Unit price in USD',                                    false, false, '29.99, 99.99'),
    ('supplier',    'VARCHAR', 'Name of the supplier',                                 false, false, 'Acme Corp'),
    ('stock_count', 'INTEGER', 'Current inventory count',                              false, false, '150, 42, 0')
) AS col(column_name, data_type, description, is_primary_key, is_joinable, sample_values)
WHERE d.name = 'products'
ON CONFLICT DO NOTHING;

-- ──────────────────────────────────────────────────────
-- Update triggers for updated_at columns
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

DROP TRIGGER IF EXISTS update_data_sources_updated_at ON data_sources;
CREATE TRIGGER update_data_sources_updated_at
    BEFORE UPDATE ON data_sources
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_dashboards_updated_at ON dashboards;
CREATE TRIGGER update_dashboards_updated_at
    BEFORE UPDATE ON dashboards
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_dashboard_widgets_updated_at ON dashboard_widgets;
CREATE TRIGGER update_dashboard_widgets_updated_at
    BEFORE UPDATE ON dashboard_widgets
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
