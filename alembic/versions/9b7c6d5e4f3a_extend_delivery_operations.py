"""Rozszerz moduł obsługi dostaw o odbiory, zadania i pliki.

Revision ID: 9b7c6d5e4f3a
Revises: 8f1d2c3b4a5e
Create Date: 2026-05-15 16:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9b7c6d5e4f3a"
down_revision: str | Sequence[str] | None = "8f1d2c3b4a5e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "delivery_case",
        sa.Column("case_type", sa.Text(), nullable=False, server_default="delivery"),
        schema="ctip",
    )
    op.create_check_constraint(
        "delivery_case_type_check",
        "delivery_case",
        "case_type in ('delivery','pickup')",
        schema="ctip",
    )
    op.create_index(
        "idx_delivery_case_type_status",
        "delivery_case",
        ["case_type", "status"],
        schema="ctip",
    )

    op.add_column(
        "delivery_case_device",
        sa.Column("device_role", sa.Text(), nullable=False, server_default="delivery"),
        schema="ctip",
    )
    op.create_index(
        "idx_delivery_case_device_machine",
        "delivery_case_device",
        ["firebird_machine_id"],
        schema="ctip",
    )

    op.create_table(
        "delivery_case_task",
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
        sa.Column("delivery_case_id", sa.Integer(), nullable=False),
        sa.Column("task_type", sa.Text(), nullable=False, server_default="other"),
        sa.Column("status", sa.Text(), nullable=False, server_default="todo"),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("due_time_window", sa.Text(), nullable=True),
        sa.Column("assignee_user_id", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("snapshot", sa.JSON(), nullable=True),
        sa.CheckConstraint(
            "task_type in ('delivery','preparation','pickup','zerowka','customer_contact','service_order','document','other')",
            name="delivery_case_task_type_check",
        ),
        sa.CheckConstraint(
            "status in ('todo','planned','done','cancelled')",
            name="delivery_case_task_status_check",
        ),
        sa.ForeignKeyConstraint(
            ["delivery_case_id"], ["ctip.delivery_case.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["assignee_user_id"], ["ctip.admin_user.id"], ondelete="SET NULL"),
        schema="ctip",
    )
    op.create_index(
        "idx_delivery_case_task_case",
        "delivery_case_task",
        ["delivery_case_id"],
        schema="ctip",
    )
    op.create_index(
        "idx_delivery_case_task_due",
        "delivery_case_task",
        ["status", "due_date"],
        schema="ctip",
    )

    op.create_table(
        "delivery_case_file",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("timezone('utc', now())"),
        ),
        sa.Column("delivery_case_id", sa.Integer(), nullable=False),
        sa.Column("file_type", sa.Text(), nullable=False, server_default="other"),
        sa.Column("source", sa.Text(), nullable=False, server_default="upload"),
        sa.Column("file_name", sa.Text(), nullable=False),
        sa.Column("original_name", sa.Text(), nullable=True),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("uploaded_by", sa.Integer(), nullable=True),
        sa.Column("snapshot", sa.JSON(), nullable=True),
        sa.CheckConstraint(
            "source in ('upload','mailbox','generated')",
            name="delivery_case_file_source_check",
        ),
        sa.ForeignKeyConstraint(
            ["delivery_case_id"], ["ctip.delivery_case.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["uploaded_by"], ["ctip.admin_user.id"], ondelete="SET NULL"),
        schema="ctip",
    )
    op.create_index(
        "idx_delivery_case_file_case",
        "delivery_case_file",
        ["delivery_case_id"],
        schema="ctip",
    )

    op.create_table(
        "delivery_document_template",
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
        sa.Column("template_key", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("document_type", sa.Text(), nullable=False, server_default="other"),
        sa.Column("template_path", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("required_fields", sa.JSON(), nullable=True),
        sa.Column("snapshot", sa.JSON(), nullable=True),
        sa.UniqueConstraint("template_key", name="uq_delivery_document_template_key"),
        schema="ctip",
    )
    op.create_index(
        "idx_delivery_document_template_active",
        "delivery_document_template",
        ["active"],
        schema="ctip",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_delivery_document_template_active",
        table_name="delivery_document_template",
        schema="ctip",
    )
    op.drop_table("delivery_document_template", schema="ctip")
    op.drop_index("idx_delivery_case_file_case", table_name="delivery_case_file", schema="ctip")
    op.drop_table("delivery_case_file", schema="ctip")
    op.drop_index("idx_delivery_case_task_due", table_name="delivery_case_task", schema="ctip")
    op.drop_index("idx_delivery_case_task_case", table_name="delivery_case_task", schema="ctip")
    op.drop_table("delivery_case_task", schema="ctip")
    op.drop_index(
        "idx_delivery_case_device_machine",
        table_name="delivery_case_device",
        schema="ctip",
    )
    op.drop_column("delivery_case_device", "device_role", schema="ctip")
    op.drop_index("idx_delivery_case_type_status", table_name="delivery_case", schema="ctip")
    op.drop_constraint("delivery_case_type_check", "delivery_case", schema="ctip")
    op.drop_column("delivery_case", "case_type", schema="ctip")
