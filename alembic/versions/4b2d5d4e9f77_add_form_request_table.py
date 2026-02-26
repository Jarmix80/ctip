"""add form request table

Revision ID: 4b2d5d4e9f77
Revises: 15989372b89d
Create Date: 2026-02-26 16:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4b2d5d4e9f77"
down_revision: str | Sequence[str] | None = "15989372b89d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "form_request",
        sa.Column("id", sa.Integer(), primary_key=True),
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
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("customer_name", sa.Text(), nullable=False),
        sa.Column("customer_email", sa.Text(), nullable=False),
        sa.Column("customer_phone", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="GENERATED"),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("token_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sms_status", sa.Text(), nullable=True),
        sa.Column("email_status", sa.Text(), nullable=True),
        sa.Column("notification_error", sa.Text(), nullable=True),
        sa.Column("submitted_payload", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status in ('GENERATED','DISPATCHED','SUBMITTED','EXPIRED')",
            name="form_request_status_check",
        ),
        sa.UniqueConstraint("token_hash", name="uq_form_request_token_hash"),
        schema="ctip",
    )

    op.create_foreign_key(
        "form_request_created_by_fkey",
        "form_request",
        "admin_user",
        local_cols=["created_by"],
        remote_cols=["id"],
        source_schema="ctip",
        referent_schema="ctip",
        ondelete="SET NULL",
    )

    op.create_index(
        "idx_form_request_status_created",
        "form_request",
        ["status", "created_at"],
        unique=False,
        schema="ctip",
    )
    op.create_index(
        "idx_form_request_created_by",
        "form_request",
        ["created_by", "created_at"],
        unique=False,
        schema="ctip",
    )


def downgrade() -> None:
    op.drop_index("idx_form_request_created_by", table_name="form_request", schema="ctip")
    op.drop_index("idx_form_request_status_created", table_name="form_request", schema="ctip")
    op.drop_constraint(
        "form_request_created_by_fkey", "form_request", type_="foreignkey", schema="ctip"
    )
    op.drop_table("form_request", schema="ctip")
