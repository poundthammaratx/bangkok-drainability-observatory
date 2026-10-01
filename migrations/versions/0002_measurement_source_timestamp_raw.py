"""measurement source_timestamp_raw

v0.3 production hardening: preserve the original source timestamp representation (e.g. ThaiWater's
naive "2026-10-01 17:39" string) alongside the normalized, timezone-aware ``measurement_at`` — see
docs/PERSISTENT_ARCHIVE.md and ``bdo.live.base.LiveMeasurement.source_timestamp_raw``.

Purely additive: one new nullable column, no existing data touched. Written as a standalone
revision (not folded into 0001) because 0001 is already applied to the production PostgreSQL
archive (Supabase) — see docs/DATABASE_DEPLOYMENT.md.

Incident note: the first deployment attempt against production PostgreSQL failed with
``psycopg.errors.StringDataRightTruncation`` — the original revision id
(``0002_measurement_source_timestamp_raw``, 37 characters) did not fit the deployed
``alembic_version.version_num`` column (``VARCHAR(32)``). PostgreSQL's transactional DDL rolled
the whole migration back, so production was left untouched at ``0001_baseline_schema``. This
revision was never successfully applied anywhere, so its id was corrected in place (not
superseded by a new revision) to ``0002_source_ts_raw`` (18 characters). The schema operation
itself (the additive ``source_timestamp_raw`` column) is unchanged. See
tests/test_migrations.py::test_revision_ids_fit_alembic_version_column for the regression test.

Revision ID: 0002_source_ts_raw
Revises: 0001_baseline_schema
Create Date: 2026-10-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0002_source_ts_raw'
down_revision: Union[str, Sequence[str], None] = '0001_baseline_schema'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('measurements', sa.Column('source_timestamp_raw', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('measurements', 'source_timestamp_raw')
