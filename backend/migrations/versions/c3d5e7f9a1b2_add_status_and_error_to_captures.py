"""add status and error_message to captures

Revision ID: c3d5e7f9a1b2
Revises: f2a9c4b7d1e3
Create Date: 2026-09-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d5e7f9a1b2'
down_revision: Union[str, None] = 'f2a9c4b7d1e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Pipeline lifecycle marker. server_default backfills existing rows to
    # "processing" and keeps the column NOT NULL; the default is retained so it
    # matches the ORM model (Capture.status) and INSERTs without a status succeed.
    op.add_column(
        'captures',
        sa.Column('status', sa.String(), server_default='processing', nullable=False),
    )
    # Short human-readable reason a capture's pipeline failed (NULL unless failed).
    op.add_column(
        'captures',
        sa.Column('error_message', sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('captures', 'error_message')
    op.drop_column('captures', 'status')
