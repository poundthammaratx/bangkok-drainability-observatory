"""measurement source_timestamp_raw

v0.3 production hardening: preserve the original source timestamp representation (e.g. ThaiWater's
naive "2026-10-01 17:39" string) alongside the normalized, timezone-aware ``measurement_at`` — see
docs/PERSISTENT_ARCHIVE.md and ``bdo.live.base.LiveMeasurement.source_timestamp_raw``.

Purely additive: one new nullable column, no existing data touched. Written as a standalone
revision (not folded into 0001) because 0001 is already applied to the production PostgreSQL
archive (Supabase) — see docs/DATABASE_DEPLOYMENT.md.

Revision ID: 0002_measurement_source_timestamp_raw
Revises: 0001_baseline_schema
Create Date: 2026-10-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0002_measurement_source_timestamp_raw'
down_revision: Union[str, Sequence[str], None] = '0001_baseline_schema'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('measurements', sa.Column('source_timestamp_raw', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('measurements', 'source_timestamp_raw')
