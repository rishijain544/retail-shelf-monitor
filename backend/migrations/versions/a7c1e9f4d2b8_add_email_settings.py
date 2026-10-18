"""add email_settings table

The Email setup screen used to persist a verified provider key by rewriting
``backend/.env``. That only works on a machine with a writable, persistent disk — a
deployed container is read-only (Render) or ephemeral (Vercel), so the key vanished on
redeploy and alerts quietly stopped being mailed. This table makes the verified
configuration durable so email alerting keeps working after deployment.

The row is a singleton (id = 1) and is applied over the environment variables at
startup, so operators who configure mail through env vars keep working unchanged.

Revision ID: a7c1e9f4d2b8
Revises: b8f1c4d7e2a6
Create Date: 2026-10-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7c1e9f4d2b8'
down_revision: Union[str, None] = 'b8f1c4d7e2a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'email_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('transport', sa.String(length=10), nullable=True),
        sa.Column('resend_api_key', sa.String(length=255), nullable=True),
        sa.Column('email_from', sa.String(length=320), nullable=True),
        sa.Column('alert_email_to', sa.String(length=1000), nullable=True),
        sa.Column('smtp_host', sa.String(length=255), nullable=True),
        sa.Column('smtp_port', sa.Integer(), nullable=True),
        sa.Column('smtp_user', sa.String(length=320), nullable=True),
        sa.Column('smtp_password', sa.String(length=512), nullable=True),
        sa.Column('smtp_from', sa.String(length=320), nullable=True),
        sa.Column(
            'smtp_use_tls', sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            'smtp_use_ssl', sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('email_settings')
