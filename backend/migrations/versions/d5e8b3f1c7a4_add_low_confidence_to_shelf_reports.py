"""add low_confidence flag to shelf_reports

A capture where the detector returns zero detections has no trustworthy fill level: the
model did not find any empty space, which is NOT evidence that the shelf is full. The
analyzer still yields a numeric fill for that case (vacuously 1.0), so rather than making
fill_percentage nullable — which would break every consumer that assumes a clean float
(trends chart, dashboard sorting/filters, CSV export) — the row carries an explicit
``low_confidence`` boolean. UI and alerting treat flagged rows as "unable to assess"
instead of as a confident 100% full shelf.

Backfill: rows written while zero-detection captures were stored with urgency 'unknown'
are flagged low_confidence = 1, and their urgency is recomputed from the documented
thresholds (fill >= 0.6 -> low, >= 0.3 -> medium, else high) so the urgency column only
ever holds the three ordinary values.

Revision ID: d5e8b3f1c7a4
Revises: b4d8f1a6c2e9
Create Date: 2026-10-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5e8b3f1c7a4'
down_revision: Union[str, None] = 'b4d8f1a6c2e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('shelf_reports') as batch_op:
        # server_default so rows added by older code (which does not know the column)
        # still satisfy NOT NULL; the ORM also supplies a Python-side default.
        batch_op.add_column(
            sa.Column(
                'low_confidence',
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

    # Zero-detection captures were previously recorded with urgency 'unknown' (and a
    # NULL fill that later downgrades pinned to 1.0). Flag them and restore an
    # ordinary urgency computed from the fill thresholds the rule-based report uses.
    op.execute(
        "UPDATE shelf_reports SET low_confidence = 1 WHERE urgency = 'unknown'"
    )
    op.execute(
        "UPDATE shelf_reports SET urgency = CASE "
        "WHEN fill_percentage >= 0.6 THEN 'low' "
        "WHEN fill_percentage >= 0.3 THEN 'medium' "
        "ELSE 'high' END "
        "WHERE urgency = 'unknown'"
    )


def downgrade() -> None:
    with op.batch_alter_table('shelf_reports') as batch_op:
        batch_op.drop_column('low_confidence')
