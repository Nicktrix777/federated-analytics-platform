"""baseline metadata schema

The complete postgres-meta schema in one migration. Squashed from the former
0001..0009 chain (metadata_state, column_profiles, transcript, needs_curation,
value_lookups, and the source_type / data_type widenings) while the platform
is pre-launch: deployments start from a wiped volume, so history-preserving
migrations bought nothing. Transitional backfills from the old chain (0003's
sample_values copy, 0008's truncation) are gone — they only made sense when
upgrading a populated database.

It is SCHEMA ONLY  no seed rows except the metadata_state singleton: data
sources, datasets and columns are discovered live from Trino by the Core API's
catalog sync (SyncCatalogsFromTrino), so seeding them here would only create
phantom entries that may not match the real sources.

Requires the pgvector extension (the pgvector/pgvector:pg16 image ships it) for
the schema-RAG embedding columns.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-07-19
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
-- The source_type set must stay in parity with data_sources' check: a source
-- type that can be connected must also be registrable as a dataset (the old
-- split silently dropped every Elasticsearch/MySQL table during catalog sync).
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

-- Column metadata — fed into AI prompts. data_type is TEXT, not VARCHAR:
-- Trino renders nested Elasticsearch/Mongo fields as fully-expanded
-- ROW/ARRAY(ROW(...)) strings thousands of characters long, and any length
-- cap silently drops those columns during catalog sync.
-- Sample values live in column_profiles (written by the profiler), not here.
CREATE TABLE IF NOT EXISTS dataset_columns (
    id              SERIAL PRIMARY KEY,
    dataset_id      INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    column_name     VARCHAR(255) NOT NULL,
    data_type       TEXT NOT NULL,
    description     TEXT,
    is_primary_key  BOOLEAN DEFAULT false,
    is_joinable     BOOLEAN DEFAULT false,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    -- semantic_type: human-promoted label (NULL = unknown). The profiler
    -- writes *suggestions* to column_profiles.suggested_semantic_type;
    -- humans promote to this column via SQL.
    semantic_type   VARCHAR(50),
    -- sensitivity: gates what the profiler persists for this column.
    -- Default 'unclassified' behaves as 'public' (default-allow).
    sensitivity     VARCHAR(20) NOT NULL DEFAULT 'unclassified'
                        CONSTRAINT chk_sensitivity_values
                        CHECK (sensitivity IN ('unclassified', 'public', 'sensitive', 'forbidden'))
);

-- Profiler output — sample values and statistics, split from dataset_columns
-- so re-registration (DELETE+reinsert of columns) and profiling stay decoupled.
CREATE TABLE IF NOT EXISTS column_profiles (
    dataset_column_id INTEGER PRIMARY KEY
        REFERENCES dataset_columns(id) ON DELETE CASCADE,
    pattern           TEXT,
    suggested_semantic_type VARCHAR(50),
    distinct_count    INTEGER,
    null_fraction     REAL,
    sample_values     TEXT,           -- JSON {leaf_path: [values]}, gated by sensitivity
    stats             JSONB DEFAULT '{}',
    content_hash      TEXT NOT NULL,
    profiled_at       TIMESTAMPTZ DEFAULT NOW()
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

-- Reports + sheets (on-demand Excel report builder). A report is a named set
-- of sheet queries; downloading it executes every sheet's SQL live and streams
-- a formatted .xlsx — result rows are never persisted (same rule as widgets).
CREATE TABLE IF NOT EXISTS reports (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(255) NOT NULL,
    description TEXT DEFAULT '',
    is_active   BOOLEAN DEFAULT true,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS report_sheets (
    id             SERIAL PRIMARY KEY,
    report_id      INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    title          VARCHAR(255) NOT NULL,
    description    TEXT DEFAULT '',
    query_sql      TEXT NOT NULL,
    -- {column_name: text|integer|number|currency|percent|date|datetime} —
    -- Excel formatting hints from the AI planner; unknown columns fall back
    -- to value sniffing in the Go workbook builder.
    column_formats JSONB DEFAULT '{}',
    position       INTEGER NOT NULL DEFAULT 0,
    max_rows       INTEGER NOT NULL DEFAULT 5000,
    created_at     TIMESTAMPTZ DEFAULT NOW(),
    updated_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reports_active ON reports(is_active);
CREATE INDEX IF NOT EXISTS idx_report_sheets_report ON report_sheets(report_id);

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

DROP TRIGGER IF EXISTS update_reports_updated_at ON reports;
CREATE TRIGGER update_reports_updated_at
    BEFORE UPDATE ON reports
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_report_sheets_updated_at ON report_sheets;
CREATE TRIGGER update_report_sheets_updated_at
    BEFORE UPDATE ON report_sheets
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ── Metadata change detection ───────────────────────────────────────────────
-- Single-row version counter. The BOOLEAN PK pinned to TRUE (CHECK id) makes a
-- second row impossible, so there is exactly one version for the whole
-- deployment. Seeded here so the first read never sees an empty table.
-- Any writer to planner-relevant metadata bumps the version; the AI Engine's
-- watcher notices within its poll interval and re-runs enrichment.
CREATE TABLE IF NOT EXISTS metadata_state (
    id         BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id),
    version    BIGINT NOT NULL DEFAULT 1,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO metadata_state (id) VALUES (TRUE) ON CONFLICT (id) DO NOTHING;

-- Statement-level bump. FOR EACH STATEMENT (not ROW) means a bulk upload that
-- rewrites N columns bumps the version once, not N times — the watcher only
-- needs to know "something changed", and coarse bumps keep the count sane.
CREATE OR REPLACE FUNCTION bump_metadata_version()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE metadata_state SET version = version + 1, updated_at = NOW() WHERE id = TRUE;
    RETURN NULL;  -- statement-level AFTER trigger: return value is ignored
END;
$$ LANGUAGE plpgsql;

-- Bump triggers cover only the tables the planner reads. Deliberately NOT on:
--   - data_sources — its schema_cache churns on every ~300s catalog sync; a
--     trigger there would bump constantly. RefreshSchema issues one explicit
--     Go bump instead (it writes nothing else the planner reads).
--   - the RAG worker-output tables (dataset_embeddings,
--     query_example_embeddings) — the enrichment run itself writes them, so a
--     trigger there would be a self-triggering loop.
DROP TRIGGER IF EXISTS bump_metadata_version_datasets ON datasets;
CREATE TRIGGER bump_metadata_version_datasets
    AFTER INSERT OR UPDATE OR DELETE ON datasets
    FOR EACH STATEMENT EXECUTE FUNCTION bump_metadata_version();

DROP TRIGGER IF EXISTS bump_metadata_version_dataset_columns ON dataset_columns;
CREATE TRIGGER bump_metadata_version_dataset_columns
    AFTER INSERT OR UPDATE OR DELETE ON dataset_columns
    FOR EACH STATEMENT EXECUTE FUNCTION bump_metadata_version();

DROP TRIGGER IF EXISTS bump_metadata_version_table_relationships ON table_relationships;
CREATE TRIGGER bump_metadata_version_table_relationships
    AFTER INSERT OR UPDATE OR DELETE ON table_relationships
    FOR EACH STATEMENT EXECUTE FUNCTION bump_metadata_version();

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

-- kind/payload: structured transcript replay — classifies each turn as 'plan'
-- or 'clarification' and attaches a JSON payload so the replay loop can
-- reconstruct typed ChatMessage objects instead of a flat text summary.
CREATE TABLE IF NOT EXISTS conversation_turns (
    id              SERIAL PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    question        TEXT NOT NULL,
    sql             TEXT,
    row_count       INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind            VARCHAR(20) NOT NULL DEFAULT 'plan'
                        CHECK (kind IN ('plan', 'clarification')),
    payload         JSONB
);
CREATE INDEX IF NOT EXISTS idx_conversation_turns_conv
    ON conversation_turns (conversation_id, created_at);

-- Curation queue — AI queries that could not be auto-resolved (repair attempts
-- exhausted, or a zero-row result that couldn't be corrected) so an operator
-- can review and fix underlying data issues. Status is flipped to 'resolved'
-- via SQL; GET /api/curation lists what needs attention.
CREATE TABLE IF NOT EXISTS needs_curation (
    id              SERIAL PRIMARY KEY,
    kind            VARCHAR(40) NOT NULL,
    dataset_id      INT NULL REFERENCES datasets(id) ON DELETE SET NULL,
    column_name     TEXT,
    question        TEXT,
    detail          TEXT,
    status          VARCHAR(20) NOT NULL DEFAULT 'new'
                        CHECK (status IN ('new', 'resolved')),
    request_id      UUID,
    conversation_id UUID,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS needs_curation_status_idx
    ON needs_curation (status);

-- ── Value lookups — coded-column → reference-table mapping ──────────────────
-- Maps a coded column (e.g. nationality storing 'IND') to a Trino-reachable
-- reference table (e.g. postgresql.reference.countries) so the AI writes a
-- subquery instead of guessing a literal.
--
-- Bindings are **string-bound, not FK-based**: upload re-registration
-- DELETEs+reinserts dataset_columns; a CASCADE would destroy curated bindings.
-- A lookup is either bound explicitly by (column_trino_path, column_name) or
-- implicitly by semantic_type (auto-expands at render time against all columns
-- in the bundle that share that semantic_type).
CREATE TABLE IF NOT EXISTS value_lookups (
    id                SERIAL PRIMARY KEY,
    column_trino_path TEXT,
    column_name       VARCHAR(255),
    semantic_type     VARCHAR(50),
    lookup_trino_path TEXT         NOT NULL,
    key_column        VARCHAR(255) NOT NULL,
    match_columns     TEXT[],
    description       TEXT,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    -- A lookup must be bound to either a semantic type (auto-expand) or an
    -- explicit column path+name. Both may be set (explicit + type fallback).
    CONSTRAINT value_lookups_binding_check CHECK (
        semantic_type IS NOT NULL
        OR (column_trino_path IS NOT NULL AND column_name IS NOT NULL)
    )
);

DROP TRIGGER IF EXISTS bump_metadata_version_value_lookups ON value_lookups;
CREATE TRIGGER bump_metadata_version_value_lookups
    AFTER INSERT OR UPDATE OR DELETE ON value_lookups
    FOR EACH STATEMENT EXECUTE FUNCTION bump_metadata_version();

-- ── Runtime-editable, non-secret LLM configuration ──────────────────────────
-- Single-row table holding the UI-editable LLM settings (provider-prefixed
-- model strings, base_url, fast-path toggle/threshold, per-tier RPM limits).
-- It does NOT store API keys — those stay in the environment (.env) and are
-- never written to the DB or sent over HTTP. Same BOOLEAN PK CHECK(id) trick as
-- metadata_state: exactly one row for the whole deployment.
--
-- The AI Engine overlays this row's `config` JSONB onto its env-derived
-- settings at startup and polls `version` (its own counter, NOT
-- metadata_state.version — that one triggers expensive re-embedding) to
-- hot-reload the provider/agent stack on a live change. The PUT
-- /api/llm-settings handler bumps `version` in the same UPDATE.
CREATE TABLE IF NOT EXISTS llm_settings (
    id         BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id),
    config     JSONB NOT NULL DEFAULT '{}'::jsonb,
    version    BIGINT NOT NULL DEFAULT 1,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed the singleton with an empty config so the first read never sees an empty
-- table; an empty config means "use the env-derived defaults verbatim".
INSERT INTO llm_settings (id) VALUES (TRUE) ON CONFLICT (id) DO NOTHING;
"""


DOWNGRADE_SQL = r"""
DROP TABLE IF EXISTS llm_settings;
DROP TABLE IF EXISTS value_lookups;
DROP TABLE IF EXISTS report_sheets;
DROP TABLE IF EXISTS reports;
DROP TABLE IF EXISTS needs_curation;
DROP TABLE IF EXISTS conversation_turns;
DROP TABLE IF EXISTS conversations;
DROP TABLE IF EXISTS query_example_embeddings;
DROP TABLE IF EXISTS dataset_embeddings;
DROP TABLE IF EXISTS metadata_state;
DROP TABLE IF EXISTS dashboard_widgets;
DROP TABLE IF EXISTS dashboards;
DROP TABLE IF EXISTS audit_logs;
DROP TABLE IF EXISTS table_relationships;
DROP TABLE IF EXISTS column_profiles;
DROP TABLE IF EXISTS dataset_columns;
DROP TABLE IF EXISTS datasets;
DROP TABLE IF EXISTS data_sources;
DROP FUNCTION IF EXISTS bump_metadata_version();
DROP FUNCTION IF EXISTS update_updated_at_column();
"""


def upgrade() -> None:
    # exec_driver_sql sends the SQL straight to psycopg2 without SQLAlchemy's
    # text() bind-parameter parsing — otherwise the ':0' inside the JSON default
    # '{"x":0,...}' is misread as a bind parameter. No params are passed, so
    # psycopg2 does no %-interpolation either; literal SQL is safe. That also
    # means a literal '%' anywhere in this SQL (even a comment) would break —
    # keep it percent-free.
    op.get_bind().exec_driver_sql(SCHEMA_SQL)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(DOWNGRADE_SQL)
