"""add form workflow tables

Revision ID: c8d3c6f8f3a1
Revises: 4b2d5d4e9f77
Create Date: 2026-03-16 15:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8d3c6f8f3a1"
down_revision: str | Sequence[str] | None = "4b2d5d4e9f77"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "form_workflow_case",
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
        sa.Column("form_request_id", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("stage", sa.Text(), nullable=False, server_default="FORM_SUBMITTED"),
        sa.Column("client_mode", sa.Text(), nullable=True),
        sa.Column("firebird_client_id", sa.Integer(), nullable=True),
        sa.Column("firebird_client_status", sa.Text(), nullable=True),
        sa.Column("client_payload_snapshot", sa.JSON(), nullable=True),
        sa.Column("proforma_firebird_id", sa.Integer(), nullable=True),
        sa.Column("proforma_number", sa.Text(), nullable=True),
        sa.Column("proforma_pdf_path", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "stage in ('FORM_SUBMITTED','CLIENT_READY','DEVICES_SELECTED','PROFORMA_CREATED')",
            name="form_workflow_case_stage_check",
        ),
        sa.UniqueConstraint(
            "form_request_id",
            name="uq_form_workflow_case_form_request_id",
        ),
        schema="ctip",
    )
    op.create_foreign_key(
        "form_workflow_case_form_request_id_fkey",
        "form_workflow_case",
        "form_request",
        local_cols=["form_request_id"],
        remote_cols=["id"],
        source_schema="ctip",
        referent_schema="ctip",
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "form_workflow_case_created_by_fkey",
        "form_workflow_case",
        "admin_user",
        local_cols=["created_by"],
        remote_cols=["id"],
        source_schema="ctip",
        referent_schema="ctip",
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "form_workflow_case_updated_by_fkey",
        "form_workflow_case",
        "admin_user",
        local_cols=["updated_by"],
        remote_cols=["id"],
        source_schema="ctip",
        referent_schema="ctip",
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_form_workflow_case_form_request",
        "form_workflow_case",
        ["form_request_id"],
        unique=False,
        schema="ctip",
    )

    op.create_table(
        "form_workflow_device",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("timezone('utc', now())"),
        ),
        sa.Column("workflow_case_id", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False, server_default="google_sheet"),
        sa.Column("source_row", sa.Integer(), nullable=True),
        sa.Column("producer", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("serial", sa.Text(), nullable=True),
        sa.Column("ewidencja", sa.Text(), nullable=True),
        sa.Column("device_status", sa.Text(), nullable=True),
        sa.Column("reservation_status", sa.Text(), nullable=True),
        sa.Column("price", sa.Text(), nullable=True),
        sa.Column("firebird_machine_id", sa.Integer(), nullable=True),
        sa.Column("firebird_client_id", sa.Integer(), nullable=True),
        sa.Column("snapshot", sa.JSON(), nullable=True),
        sa.CheckConstraint(
            "source_type in ('google_sheet','firebird_magazyn_28')",
            name="form_workflow_device_source_type_check",
        ),
        sa.UniqueConstraint(
            "workflow_case_id",
            "source_type",
            "source_row",
            name="uq_form_workflow_device_source_row",
        ),
        schema="ctip",
    )
    op.create_foreign_key(
        "form_workflow_device_workflow_case_id_fkey",
        "form_workflow_device",
        "form_workflow_case",
        local_cols=["workflow_case_id"],
        remote_cols=["id"],
        source_schema="ctip",
        referent_schema="ctip",
        ondelete="CASCADE",
    )
    op.create_index(
        "idx_form_workflow_device_case",
        "form_workflow_device",
        ["workflow_case_id"],
        unique=False,
        schema="ctip",
    )


def downgrade() -> None:
    op.drop_index("idx_form_workflow_device_case", table_name="form_workflow_device", schema="ctip")
    op.drop_constraint(
        "form_workflow_device_workflow_case_id_fkey",
        "form_workflow_device",
        type_="foreignkey",
        schema="ctip",
    )
    op.drop_table("form_workflow_device", schema="ctip")

    op.drop_index(
        "idx_form_workflow_case_form_request", table_name="form_workflow_case", schema="ctip"
    )
    op.drop_constraint(
        "form_workflow_case_updated_by_fkey",
        "form_workflow_case",
        type_="foreignkey",
        schema="ctip",
    )
    op.drop_constraint(
        "form_workflow_case_created_by_fkey",
        "form_workflow_case",
        type_="foreignkey",
        schema="ctip",
    )
    op.drop_constraint(
        "form_workflow_case_form_request_id_fkey",
        "form_workflow_case",
        type_="foreignkey",
        schema="ctip",
    )
    op.drop_table("form_workflow_case", schema="ctip")
