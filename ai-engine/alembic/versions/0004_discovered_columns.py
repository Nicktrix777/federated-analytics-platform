"""discovered_columns - live-sampled schema cache for Tally/Zoho Books

Both the Tally and Zoho Books Trino connectors previously reported a fixed,
hand-written Java schema (TallyEntity/ZohoEntity enums) regardless of what a
customer's real data actually contains. This table is the cache the Java
plugins (trino-plugins/tally, trino-plugins/zoho-books) read/write directly
via plain JDBC - same access pattern as their existing MetadataStore - to
back genuine live-sample-based schema discovery, matching what Trino's own
MongoDB connector does when there is no explicit _schema collection.

Deliberately holds ONLY derived schema shape (names + types) - no literal
value column exists on this table at all, matching the same "never persist a
real customer value" intent as the sample_values removal in 0003.

Revision ID: 0004_discovered_columns
Revises: 0003_no_literals
Create Date: 2026-08-11
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0004_discovered_columns"
down_revision: Union[str, None] = "0003_no_literals"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


UPGRADE_SQL = r"""
CREATE TABLE IF NOT EXISTS discovered_columns (
    id             SERIAL PRIMARY KEY,
    data_source_id INTEGER NOT NULL REFERENCES data_sources(id) ON DELETE CASCADE,
    table_name     VARCHAR(100) NOT NULL,
    column_name    VARCHAR(200) NOT NULL,
    type_name      VARCHAR(20) NOT NULL,
    is_attribute   BOOLEAN NOT NULL DEFAULT false,
    discovered_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (data_source_id, table_name, column_name)
);

CREATE INDEX IF NOT EXISTS idx_discovered_columns_lookup
    ON discovered_columns (data_source_id, table_name);
"""


DOWNGRADE_SQL = r"""
DROP TABLE IF EXISTS discovered_columns;
"""


def upgrade() -> None:
    op.get_bind().exec_driver_sql(UPGRADE_SQL)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(DOWNGRADE_SQL)
