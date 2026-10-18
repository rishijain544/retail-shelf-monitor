"""add recommended_action to shelf_reports

Revision ID: f2a9c4b7d1e3
Revises: e71b2d56a38f
Create Date: 2026-08-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2a9c4b7d1e3'
down_revision: Union[str, None] = 'e71b2d56a38f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'shelf_reports',
        sa.Column('recommended_action', sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('shelf_reports', 'recommended_action')
