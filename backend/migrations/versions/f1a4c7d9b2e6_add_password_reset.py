"""Add password reset tokens and users.password_changed_at

Two pieces of the forgot-password flow:

* ``users.password_changed_at`` — lets a reset invalidate every JWT issued before it.
  NULL for existing rows, so nobody is signed out by the upgrade (their passwords were
  never reset).
* ``password_reset_tokens`` — one row per emailed link, holding only the SHA-256 digest
  of the token, its expiry, and whether it has been used.

Deliberately portable: ``op.batch_alter_table`` for the ALTER, ``sa.func.now()`` for the
default and no raw PostgreSQL-only SQL, so the same revision applies to SQLite (local
development and the pytest suite) and PostgreSQL (compose and production). A previous
revision wrote an integer into a boolean column with raw SQL; that passed on SQLite and
aborted the whole chain on a fresh PostgreSQL volume.

Revision ID: f1a4c7d9b2e6
Revises: c9f4a2d6e8b1
Create Date: 2026-11-01
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "f1a4c7d9b2e6"
down_revision: Union[str, None] = "c9f4a2d6e8b1"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True)
        )

    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("requested_ip", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("token_hash", name="uq_password_reset_tokens_token_hash"),
    )
    op.create_index("ix_password_reset_tokens_id", "password_reset_tokens", ["id"])
    op.create_index(
        "ix_password_reset_tokens_user_id", "password_reset_tokens", ["user_id"]
    )
    op.create_index(
        "ix_password_reset_tokens_token_hash",
        "password_reset_tokens",
        ["token_hash"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_password_reset_tokens_token_hash", table_name="password_reset_tokens"
    )
    op.drop_index(
        "ix_password_reset_tokens_user_id", table_name="password_reset_tokens"
    )
    op.drop_index("ix_password_reset_tokens_id", table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")

    with op.batch_alter_table("users") as batch:
        batch.drop_column("password_changed_at")
