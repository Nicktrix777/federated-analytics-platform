-- ======================================================
-- Migration 001 — data_sources registry + table_relationships
--
-- Brings a v1 postgres-meta database (datasets, dataset_columns,
-- audit_logs only) up to date with the v2 schema in
-- init/postgres-meta-init.sql. The init SQL only runs on a FRESH
-- postgres volume, so existing environments need this migration.
--
-- Idempotent: safe to run multiple times.
-- ======================================================

CREATE TABLE IF NOT EXISTS data_sources (
    id                  SERIAL PRIMARY KEY,
    name                VARCHAR(255) NOT NULL UNIQUE,
    source_type         VARCHAR(50) NOT NULL
                            CHECK (source_type IN ('postgresql', 'mongodb', 'elasticsearch', 'mysql', 'trino')),
    host                VARCHAR(255) NOT NULL,
    port                INTEGER NOT NULL,
    database_name       VARCHAR(255),
    username            VARCHAR(255),
    password_encrypted  TEXT,
    extra_config        JSONB DEFAULT '{}',
    trino_catalog       VARCHAR(100) NOT NULL UNIQUE,
    is_active           BOOLEAN DEFAULT true,
    schema_cache        JSONB DEFAULT '{}',
    last_schema_refresh TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS table_relationships (
    id              SERIAL PRIMARY KEY,
    from_trino_path VARCHAR(500) NOT NULL,
    from_column     VARCHAR(255) NOT NULL,
    to_trino_path   VARCHAR(500) NOT NULL,
    to_column       VARCHAR(255) NOT NULL,
    join_type       VARCHAR(50) DEFAULT 'INNER',
    cast_expression VARCHAR(255),
    description     TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_data_sources_active ON data_sources(is_active);

-- updated_at trigger (function may already exist from init SQL)
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS update_data_sources_updated_at ON data_sources;
CREATE TRIGGER update_data_sources_updated_at
    BEFORE UPDATE ON data_sources
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Seed the built-in sources that map to the existing Trino catalogs
INSERT INTO data_sources (name, source_type, host, port, database_name, trino_catalog, is_active)
VALUES
    ('PostgreSQL Source', 'postgresql', 'postgres-source', 5432, 'source_db', 'postgres_source', true),
    ('MongoDB Source',    'mongodb',    'mongo-source',    27017, 'employee_db', 'mongodb', true)
ON CONFLICT (name) DO NOTHING;
