"""baseline metadata schema

The complete postgres-meta schema in one migration. This is the single source
of truth that replaced init/postgres-meta-init.sql + scripts/migrate.sh +
scripts/migrations/001..007. It is SCHEMA ONLY — no seed rows: data sources,
datasets and columns are discovered live from Trino by the Core API's catalog
sync (SyncCatalogsFromTrino), so seeding them here would only create phantom
entries that may not match the real sources.

Requires the pgvector extension (the pgvector/pgvector:pg16 image ships it) for
the schema-RAG embedding columns.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-07-13
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA_SQL = r"""
CREATE EXTENSION IF NOT EXISTS vector;

-- Data Sources Registry — registered connections, one per Trino catalog.
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

-- Dataset Registry — known tables/collections/indices with their Trino mapping.
CREATE TABLE IF NOT EXISTS datasets (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(255) NOT NULL UNIQUE,
    description     TEXT,
    source_type     VARCHAR(50) NOT NULL CHECK (source_type IN ('postgresql', 'mongodb', 'elasticsearch', 'mysql', 'trino')),
    trino_catalog   VARCHAR(100) NOT NULL,
    trino_schema    VARCHAR(100) NOT NULL,
    trino_table     VARCHAR(100) NOT NULL,
    is_active       BOOLEAN DEFAULT true,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Column metadata — fed into AI prompts (data_type holds the full Trino type,
-- including nested ROW/ARRAY(ROW) definitions).
CREATE TABLE IF NOT EXISTS dataset_columns (
    id              SERIAL PRIMARY KEY,
    dataset_id      INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    column_name     VARCHAR(255) NOT NULL,
    data_type       TEXT NOT NULL,
    description     TEXT,
    is_primary_key  BOOLEAN DEFAULT false,
    is_joinable     BOOLEAN DEFAULT false,
    sample_values   TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Cross-source join hints for the AI planner.
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

-- Audit log — every query request logged by the Core API.
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

-- Dashboards + widgets (dashboard builder).
CREATE TABLE IF NOT EXISTS dashboards (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(255) NOT NULL,
    description TEXT DEFAULT '',
    layout      JSONB DEFAULT '{}',
    is_active   BOOLEAN DEFAULT true,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS dashboard_widgets (
    id              SERIAL PRIMARY KEY,
    dashboard_id    INTEGER NOT NULL REFERENCES dashboards(id) ON DELETE CASCADE,
    title           VARCHAR(255) NOT NULL,
    query_sql       TEXT NOT NULL,
    chart_type      VARCHAR(50) DEFAULT 'table'
                        CHECK (chart_type IN ('table', 'bar', 'line', 'pie', 'area', 'scatter', 'number', 'gauge')),
    chart_config    JSONB DEFAULT '{}',
    grid_position   JSONB DEFAULT '{"x":0,"y":0,"w":6,"h":4}',
    refresh_rate_ms INTEGER DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_status ON audit_logs(status);
CREATE INDEX IF NOT EXISTS idx_dataset_columns_dataset_id ON dataset_columns(dataset_id);
CREATE INDEX IF NOT EXISTS idx_dashboards_active ON dashboards(is_active);
CREATE INDEX IF NOT EXISTS idx_dashboard_widgets_dashboard ON dashboard_widgets(dashboard_id);
CREATE INDEX IF NOT EXISTS idx_data_sources_active ON data_sources(is_active);

-- updated_at trigger
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

-- Schema RAG (Phase 1): one embedding row per active dataset.
CREATE TABLE IF NOT EXISTS dataset_embeddings (
    dataset_id   INTEGER PRIMARY KEY REFERENCES datasets(id) ON DELETE CASCADE,
    embedding    vector(1536) NOT NULL,
    embed_text   TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    model        TEXT NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_dataset_embeddings_vec
    ON dataset_embeddings USING hnsw (embedding vector_cosine_ops);

-- Semantic few-shots (Phase 2): one embedding per successful AI query.
CREATE TABLE IF NOT EXISTS query_example_embeddings (
    audit_log_id INTEGER PRIMARY KEY REFERENCES audit_logs(id) ON DELETE CASCADE,
    question     TEXT NOT NULL,
    sql_executed TEXT NOT NULL,
    embedding    vector(1536) NOT NULL,
    model        TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_query_example_embeddings_vec
    ON query_example_embeddings USING hnsw (embedding vector_cosine_ops);

-- Conversation / multi-turn memory (Phase 3).
CREATE TABLE IF NOT EXISTS conversations (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title          TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_active_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS conversation_turns (
    id              SERIAL PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    question        TEXT NOT NULL,
    sql             TEXT,
    row_count       INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_conversation_turns_conv
    ON conversation_turns (conversation_id, created_at);
"""


DOWNGRADE_SQL = r"""
DROP TABLE IF EXISTS conversation_turns;
DROP TABLE IF EXISTS conversations;
DROP TABLE IF EXISTS query_example_embeddings;
DROP TABLE IF EXISTS dataset_embeddings;
DROP TABLE IF EXISTS dashboard_widgets;
DROP TABLE IF EXISTS dashboards;
DROP TABLE IF EXISTS audit_logs;
DROP TABLE IF EXISTS table_relationships;
DROP TABLE IF EXISTS dataset_columns;
DROP TABLE IF EXISTS datasets;
DROP TABLE IF EXISTS data_sources;
DROP FUNCTION IF EXISTS update_updated_at_column();
"""


def upgrade() -> None:
    # exec_driver_sql sends the SQL straight to psycopg2 without SQLAlchemy's
    # text() bind-parameter parsing — otherwise the ':0' inside the JSON default
    # '{"x":0,...}' is misread as a bind parameter. No params are passed, so
    # psycopg2 does no %-interpolation either; literal SQL is safe.
    op.get_bind().exec_driver_sql(SCHEMA_SQL)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(DOWNGRADE_SQL)
