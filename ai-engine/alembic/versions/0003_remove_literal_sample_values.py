"""remove literal customer-data persistence (column_profiles.sample_values)

column_profiles.sample_values stored real example values sampled from
customer data (gated by a sensitivity classifier) for every connector. That's
in tension with the platform's "we don't take your data" position regardless
of gating, so the column is dropped outright rather than left always-NULL —
there should be no code path capable of holding a literal customer value.
Derived-only signals (pattern, suggested_semantic_type, distinct_count,
null_fraction, and the newly-populated per-leaf `stats` JSONB) are untouched
and continue to give the AI planner filter-shape guidance without literals.

dataset_embeddings previously baked those same literals into embed_text/the
vector itself (ai-engine/embeddings.py) - existing rows are cleared so no
literal-derived embedding lingers; the next reindex pass (triggered by the
metadata-version bump below) recomputes every embedding from the
now-literal-free profiler output.

Revision ID: 0003_no_literals
Revises: 0002_zoho_tally
Create Date: 2026-08-11
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0003_no_literals"
down_revision: Union[str, None] = "0002_zoho_tally"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


UPGRADE_SQL = r"""
ALTER TABLE column_profiles DROP COLUMN IF EXISTS sample_values;

DELETE FROM dataset_embeddings;

UPDATE metadata_state SET version = version + 1, updated_at = NOW() WHERE id = TRUE;
"""


DOWNGRADE_SQL = r"""
ALTER TABLE column_profiles ADD COLUMN IF NOT EXISTS sample_values TEXT;
"""


def upgrade() -> None:
    op.get_bind().exec_driver_sql(UPGRADE_SQL)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(DOWNGRADE_SQL)
