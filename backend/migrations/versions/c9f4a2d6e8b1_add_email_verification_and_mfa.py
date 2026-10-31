"""Add email verification and MFA to users; add the code tables.

Revision ID: c9f4a2d6e8b1
Revises: a7c1e9f4d2b8
Create Date: 2026-10-21

Existing accounts are grandfathered as verified (``email_verified = true``) and left
unenrolled (``mfa_enabled = false``), so nobody is locked out by the upgrade: they can
still sign in with their password and are asked to enrol an authenticator on that
first sign-in.

Done through ``op.batch_alter_table`` and SQLAlchemy Core, never raw Postgres SQL, so
the same revision applies to SQLite for local development.
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "c9f4a2d6e8b1"
down_revision: Union[str, None] = "a7c1e9f4d2b8"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column(
                "email_verified",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(
            sa.Column(
                "mfa_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(sa.Column("mfa_secret_encrypted", sa.String(), nullable=True))
        batch.add_column(sa.Column("mfa_last_used_step", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column(
                "mfa_exempt",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(
            sa.Column(
                "failed_login_attempts",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch.add_column(
            sa.Column("login_locked_until", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "failed_mfa_attempts",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch.add_column(
            sa.Column("mfa_locked_until", sa.DateTime(timezone=True), nullable=True)
        )

    # Every account that exists today has been reachable with its password all along.
    # Leaving them unverified would lock out every user of a running deployment the
    # moment this revision lands.
    users = sa.table(
        "users",
        sa.column("email_verified", sa.Boolean()),
    )
    op.execute(users.update().values(email_verified=True))

    op.create_table(
        "email_verification_codes",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_email_verification_codes_user_id",
        "email_verification_codes",
        ["user_id"],
    )
    op.create_index(
        "ix_email_verification_codes_id",
        "email_verification_codes",
        ["id"],
    )

    op.create_table(
        "mfa_recovery_codes",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("code_hash", sa.String(length=255), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_mfa_recovery_codes_user_id", "mfa_recovery_codes", ["user_id"])
    op.create_index("ix_mfa_recovery_codes_id", "mfa_recovery_codes", ["id"])


def downgrade() -> None:
    op.drop_index("ix_mfa_recovery_codes_id", table_name="mfa_recovery_codes")
    op.drop_index("ix_mfa_recovery_codes_user_id", table_name="mfa_recovery_codes")
    op.drop_table("mfa_recovery_codes")

    op.drop_index(
        "ix_email_verification_codes_id", table_name="email_verification_codes"
    )
    op.drop_index(
        "ix_email_verification_codes_user_id", table_name="email_verification_codes"
    )
    op.drop_table("email_verification_codes")

    with op.batch_alter_table("users") as batch:
        batch.drop_column("mfa_locked_until")
        batch.drop_column("failed_mfa_attempts")
        batch.drop_column("login_locked_until")
        batch.drop_column("failed_login_attempts")
        batch.drop_column("mfa_exempt")
        batch.drop_column("mfa_last_used_step")
        batch.drop_column("mfa_secret_encrypted")
        batch.drop_column("mfa_enabled")
        batch.drop_column("email_verified")
