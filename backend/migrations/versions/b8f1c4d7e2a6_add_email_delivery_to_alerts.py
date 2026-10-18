"""add email delivery columns to alerts

High-urgency alerts already record `sent_to_slack`; the same alert is now also mailed
to the store's login address(es) through SMTP. Two columns keep the dashboard honest
about both channels:

* ``sent_to_email``   — True when at least one email was delivered.
* ``email_recipients`` — comma-joined addresses that actually received the alert.

server_default keeps rows written by older code (which does not know the column)
NOT NULL-safe; existing rows were never emailed, so they default to false/NULL.

Revision ID: b8f1c4d7e2a6
Revises: d5e8b3f1c7a4
Create Date: 2026-10-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8f1c4d7e2a6'
down_revision: Union[str, None] = 'd5e8b3f1c7a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('alerts') as batch_op:
        batch_op.add_column(
            sa.Column(
                'sent_to_email',
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column('email_recipients', sa.String(length=1000), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table('alerts') as batch_op:
        batch_op.drop_column('email_recipients')
        batch_op.drop_column('sent_to_email')
