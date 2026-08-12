"""zoho_books/tally source types, per-datasource trino schema, tenant_id

Adds the two new source types needed for native Trino connector plugins
(Zoho Books, Tally) that front live external APIs rather than a JDBC/wire
source. Both share a single static Trino catalog across every registered
datasource of that type (one catalog file, never touched again as customers
are added) - so unlike postgresql/mongodb/elasticsearch/mysql, a single
Trino catalog can now back MANY data_sources rows, one per customer's Zoho
org / Tally company, distinguished by trino_schema. That breaks the old
"one catalog = one datasource" assumption baked into the unique constraint
on trino_catalog alone, so it's widened to (trino_catalog, trino_schema).

Also adds a tenant_id column to the four top-level customer-owned tables
(data_sources, reports, dashboards, conversations) - forward-compatible
placeholder for a future SaaS tier, not wired into any query path yet.
Everything defaults to a single 'default' tenant, so this is a no-op for
today's single-tenant self-hosted deployments.

Revision ID: 0002_zoho_tally
Revises: 0001_baseline
Create Date: 2026-08-06
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002_zoho_tally"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


UPGRADE_SQL = r"""
ALTER TABLE data_sources DROP CONSTRAINT IF EXISTS data_sources_source_type_check;
ALTER TABLE data_sources ADD CONSTRAINT data_sources_source_type_check
    CHECK (source_type IN ('postgresql', 'mongodb', 'elasticsearch', 'mysql', 'trino', 'zoho_books', 'tally'));

ALTER TABLE datasets DROP CONSTRAINT IF EXISTS datasets_source_type_check;
ALTER TABLE datasets ADD CONSTRAINT datasets_source_type_check
    CHECK (source_type IN ('postgresql', 'mongodb', 'elasticsearch', 'mysql', 'trino', 'zoho_books', 'tally'));

-- One catalog, many schemas: each Zoho org / Tally company registered as its
-- own data_sources row shares the same trino_catalog and is distinguished by
-- trino_schema. Defaults to '' (not NULL) for existing source types, which
-- still have exactly one schema per catalog - Postgres treats NULL as
-- distinct from NULL in a unique constraint, so a NULL default here would
-- silently let two rows share the same trino_catalog again.
ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS trino_schema VARCHAR(100) NOT NULL DEFAULT '';

ALTER TABLE data_sources DROP CONSTRAINT IF EXISTS data_sources_trino_catalog_key;
ALTER TABLE data_sources ADD CONSTRAINT data_sources_trino_catalog_trino_schema_key
    UNIQUE (trino_catalog, trino_schema);

ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(100) NOT NULL DEFAULT 'default';
ALTER TABLE reports ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(100) NOT NULL DEFAULT 'default';
ALTER TABLE dashboards ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(100) NOT NULL DEFAULT 'default';
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(100) NOT NULL DEFAULT 'default';
"""


DOWNGRADE_SQL = r"""
ALTER TABLE conversations DROP COLUMN IF EXISTS tenant_id;
ALTER TABLE dashboards DROP COLUMN IF EXISTS tenant_id;
ALTER TABLE reports DROP COLUMN IF EXISTS tenant_id;
ALTER TABLE data_sources DROP COLUMN IF EXISTS tenant_id;

ALTER TABLE data_sources DROP CONSTRAINT IF EXISTS data_sources_trino_catalog_trino_schema_key;
ALTER TABLE data_sources ADD CONSTRAINT data_sources_trino_catalog_key UNIQUE (trino_catalog);
ALTER TABLE data_sources DROP COLUMN IF EXISTS trino_schema;

ALTER TABLE datasets DROP CONSTRAINT IF EXISTS datasets_source_type_check;
ALTER TABLE datasets ADD CONSTRAINT datasets_source_type_check
    CHECK (source_type IN ('postgresql', 'mongodb', 'elasticsearch', 'mysql', 'trino'));

ALTER TABLE data_sources DROP CONSTRAINT IF EXISTS data_sources_source_type_check;
ALTER TABLE data_sources ADD CONSTRAINT data_sources_source_type_check
    CHECK (source_type IN ('postgresql', 'mongodb', 'elasticsearch', 'mysql', 'trino'));
"""


def upgrade() -> None:
    # exec_driver_sql bypasses SQLAlchemy's text() bind-parameter parsing and
    # is called with no params, so no percent-interpolation happens either -
    # but that also means a literal '%' anywhere in this SQL, even a comment,
    # would be misread. Keep it percent-free (see 0001_baseline_schema.py).
    op.get_bind().exec_driver_sql(UPGRADE_SQL)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(DOWNGRADE_SQL)
