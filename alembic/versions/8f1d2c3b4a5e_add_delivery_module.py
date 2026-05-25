"""Dodaj moduł obsługi dostaw i kalendarz końców umów GRENKE.

Revision ID: 8f1d2c3b4a5e
Revises: b8c3e2d91a7f
Create Date: 2026-05-14 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8f1d2c3b4a5e"
down_revision: str | Sequence[str] | None = "b8c3e2d91a7f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE ctip.admin_user DROP CONSTRAINT IF EXISTS admin_user_role_check")
    op.create_check_constraint(
        "admin_user_role_check",
        "admin_user",
        "role in ('admin','operator','serwisant')",
        schema="ctip",
    )

    op.create_table(
        "delivery_case",
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
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False, server_default="manual"),
        sa.Column("status", sa.Text(), nullable=False, server_default="new"),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("form_request_id", sa.Integer(), nullable=True),
        sa.Column("workflow_case_id", sa.Integer(), nullable=True),
        sa.Column("firebird_client_id", sa.Integer(), nullable=True),
        sa.Column("customer_name", sa.Text(), nullable=True),
        sa.Column("customer_nip", sa.Text(), nullable=True),
        sa.Column("customer_email", sa.Text(), nullable=True),
        sa.Column("customer_phone", sa.Text(), nullable=True),
        sa.Column("delivery_date", sa.Date(), nullable=True),
        sa.Column("delivery_time_window", sa.Text(), nullable=True),
        sa.Column("delivery_contact_name", sa.Text(), nullable=True),
        sa.Column("delivery_contact_phone", sa.Text(), nullable=True),
        sa.Column("delivery_notes", sa.Text(), nullable=True),
        sa.Column("service_notes", sa.Text(), nullable=True),
        sa.Column("snapshot", sa.JSON(), nullable=True),
        sa.CheckConstraint("source in ('grenke','manual')", name="delivery_case_source_check"),
        sa.CheckConstraint(
            "status in ('new','planned','in_progress','done','cancelled')",
            name="delivery_case_status_check",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["ctip.admin_user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["ctip.admin_user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["form_request_id"], ["ctip.form_request.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["workflow_case_id"], ["ctip.form_workflow_case.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("workflow_case_id", name="uq_delivery_case_workflow_case_id"),
        schema="ctip",
    )
    op.create_index(
        "idx_delivery_case_source_status",
        "delivery_case",
        ["source", "status"],
        schema="ctip",
    )
    op.create_index(
        "idx_delivery_case_delivery_date",
        "delivery_case",
        ["delivery_date"],
        schema="ctip",
    )
    op.create_index(
        "idx_delivery_case_firebird_client",
        "delivery_case",
        ["firebird_client_id"],
        schema="ctip",
    )

    op.create_table(
        "delivery_case_device",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("timezone('utc', now())"),
        ),
        sa.Column("delivery_case_id", sa.Integer(), nullable=False),
        sa.Column("workflow_device_id", sa.Integer(), nullable=True),
        sa.Column("producer", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("serial", sa.Text(), nullable=True),
        sa.Column("ewidencja", sa.Text(), nullable=True),
        sa.Column("firebird_machine_id", sa.Integer(), nullable=True),
        sa.Column("source_type", sa.Text(), nullable=True),
        sa.Column("source_row", sa.Integer(), nullable=True),
        sa.Column("snapshot", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["delivery_case_id"], ["ctip.delivery_case.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workflow_device_id"], ["ctip.form_workflow_device.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint(
            "delivery_case_id",
            "workflow_device_id",
            name="uq_delivery_case_device_workflow_device_id",
        ),
        schema="ctip",
    )
    op.create_index(
        "idx_delivery_case_device_case",
        "delivery_case_device",
        ["delivery_case_id"],
        schema="ctip",
    )

    op.create_table(
        "grenke_contract_end",
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
        sa.Column("delivery_case_id", sa.Integer(), nullable=True),
        sa.Column("form_request_id", sa.Integer(), nullable=True),
        sa.Column("workflow_case_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending_confirmation"),
        sa.Column("prefilled_end_date", sa.Date(), nullable=True),
        sa.Column("confirmed_end_date", sa.Date(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by", sa.Integer(), nullable=True),
        sa.Column("customer_name", sa.Text(), nullable=True),
        sa.Column("contract_number", sa.Text(), nullable=True),
        sa.Column("source_note", sa.Text(), nullable=True),
        sa.Column("notification_history", sa.JSON(), nullable=True),
        sa.Column("snapshot", sa.JSON(), nullable=True),
        sa.CheckConstraint(
            "status in ('pending_confirmation','confirmed','cancelled')",
            name="grenke_contract_end_status_check",
        ),
        sa.ForeignKeyConstraint(
            ["delivery_case_id"], ["ctip.delivery_case.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["form_request_id"], ["ctip.form_request.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["workflow_case_id"], ["ctip.form_workflow_case.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["confirmed_by"], ["ctip.admin_user.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("workflow_case_id", name="uq_grenke_contract_end_workflow_case_id"),
        schema="ctip",
    )
    op.create_index(
        "idx_grenke_contract_end_status_date",
        "grenke_contract_end",
        ["status", "confirmed_end_date"],
        schema="ctip",
    )
    op.create_index(
        "idx_grenke_contract_end_pending_prefill",
        "grenke_contract_end",
        ["status", "prefilled_end_date"],
        schema="ctip",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_grenke_contract_end_pending_prefill", table_name="grenke_contract_end", schema="ctip"
    )
    op.drop_index(
        "idx_grenke_contract_end_status_date", table_name="grenke_contract_end", schema="ctip"
    )
    op.drop_table("grenke_contract_end", schema="ctip")
    op.drop_index("idx_delivery_case_device_case", table_name="delivery_case_device", schema="ctip")
    op.drop_table("delivery_case_device", schema="ctip")
    op.drop_index("idx_delivery_case_firebird_client", table_name="delivery_case", schema="ctip")
    op.drop_index("idx_delivery_case_delivery_date", table_name="delivery_case", schema="ctip")
    op.drop_index("idx_delivery_case_source_status", table_name="delivery_case", schema="ctip")
    op.drop_table("delivery_case", schema="ctip")

    op.execute("ALTER TABLE ctip.admin_user DROP CONSTRAINT IF EXISTS admin_user_role_check")
    op.create_check_constraint(
        "admin_user_role_check",
        "admin_user",
        "role in ('admin','operator')",
        schema="ctip",
    )
