"""admin module basics

Revision ID: fa1d0aa9a3c2
Revises: e0ea7dc108d6
Create Date: 2025-10-12 10:15:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fa1d0aa9a3c2"
down_revision: str | Sequence[str] | None = "e0ea7dc108d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admin_user",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("first_name", sa.Text(), nullable=True),
        sa.Column("last_name", sa.Text(), nullable=True),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("internal_ext", sa.Text(), nullable=True),
        sa.Column("role", sa.Text(), nullable=False, server_default="admin"),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("timezone('utc', now())"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("timezone('utc', now())"),
        ),
        schema="ctip",
    )
    op.create_index("ix_admin_user_email", "admin_user", ["email"], unique=True, schema="ctip")

    op.create_table(
        "admin_session",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("timezone('utc', now())"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("client_ip", sa.Text(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        schema="ctip",
    )
    op.create_index(
        "ix_admin_session_token", "admin_session", ["token"], unique=True, schema="ctip"
    )
    op.create_foreign_key(
        "admin_session_user_id_fkey",
        "admin_session",
        "admin_user",
        local_cols=["user_id"],
        remote_cols=["id"],
        source_schema="ctip",
        referent_schema="ctip",
        ondelete="CASCADE",
    )

    op.create_table(
        "admin_setting",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("is_secret", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("timezone('utc', now())"),
        ),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        schema="ctip",
    )
    op.create_foreign_key(
        "admin_setting_updated_by_fkey",
        "admin_setting",
        "admin_user",
        local_cols=["updated_by"],
        remote_cols=["id"],
        source_schema="ctip",
        referent_schema="ctip",
        ondelete="SET NULL",
    )

    op.create_table(
        "admin_audit_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("timezone('utc', now())"),
        ),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("client_ip", sa.Text(), nullable=True),
        schema="ctip",
    )
    op.create_index(
        "ix_admin_audit_log_created_at", "admin_audit_log", ["created_at"], schema="ctip"
    )
    op.create_foreign_key(
        "admin_audit_log_user_id_fkey",
        "admin_audit_log",
        "admin_user",
        local_cols=["user_id"],
        remote_cols=["id"],
        source_schema="ctip",
        referent_schema="ctip",
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "admin_audit_log_user_id_fkey", "admin_audit_log", type_="foreignkey", schema="ctip"
    )
    op.drop_index("ix_admin_audit_log_created_at", table_name="admin_audit_log", schema="ctip")
    op.drop_table("admin_audit_log", schema="ctip")

    op.drop_constraint(
        "admin_setting_updated_by_fkey", "admin_setting", type_="foreignkey", schema="ctip"
    )
    op.drop_table("admin_setting", schema="ctip")

    op.drop_constraint(
        "admin_session_user_id_fkey", "admin_session", type_="foreignkey", schema="ctip"
    )
    op.drop_index("ix_admin_session_token", table_name="admin_session", schema="ctip")
    op.drop_table("admin_session", schema="ctip")

    op.drop_index("ix_admin_user_email", table_name="admin_user", schema="ctip")
    op.drop_table("admin_user", schema="ctip")
