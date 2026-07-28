"""Dodanie trwałego audytu spójności urządzeń.

Revision ID: 9d4b6f2a1c80
Revises: 7c91e2f4a6b8
Create Date: 2026-07-24 12:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "9d4b6f2a1c80"
down_revision: str | Sequence[str] | None = "7c91e2f4a6b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Tworzy historię przebiegów audytu i ich szczegółowe wyniki."""
    op.create_table(
        "device_audit_run",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("requested_by", sa.Integer(), nullable=True),
        sa.Column("phase", sa.Text(), nullable=True),
        sa.Column("processed_items", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_items", sa.Integer(), server_default="0", nullable=False),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("source_snapshot", sa.JSON(), nullable=True),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.timezone("utc", sa.func.now()),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.timezone("utc", sa.func.now()),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status in ('pending','running','completed','failed')",
            name="device_audit_run_status_check",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["ctip.admin_user.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="ctip",
    )
    op.create_index(
        "idx_device_audit_run_status_created",
        "device_audit_run",
        ["status", sa.text("created_at DESC")],
        schema="ctip",
    )
    op.create_table(
        "device_audit_item",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("canonical_key", sa.Text(), nullable=False),
        sa.Column("producer", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("serial", sa.Text(), nullable=True),
        sa.Column("ewidencja", sa.Text(), nullable=True),
        sa.Column("source_row", sa.Integer(), nullable=True),
        sa.Column("sheet_row", sa.Integer(), nullable=True),
        sa.Column("machine_id", sa.Integer(), nullable=True),
        sa.Column("ctip_unit_id", sa.Integer(), nullable=True),
        sa.Column("sheet_present", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("warehouse_present", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("machine_present", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("ctip_present", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("result_status", sa.Text(), nullable=False),
        sa.Column("issue_codes", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("issue_summary", sa.Text(), nullable=True),
        sa.Column("source_details", sa.JSON(), server_default="{}", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.timezone("utc", sa.func.now()),
            nullable=False,
        ),
        sa.CheckConstraint(
            "result_status in ('ok','missing','discrepancy','duplicate')",
            name="device_audit_item_result_status_check",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["ctip.device_audit_run.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "canonical_key",
            name="uq_device_audit_item_run_key",
        ),
        schema="ctip",
    )
    op.create_index(
        "idx_device_audit_item_run_result",
        "device_audit_item",
        ["run_id", "result_status"],
        schema="ctip",
    )


def downgrade() -> None:
    """Usuwa trwały audyt urządzeń."""
    op.drop_index(
        "idx_device_audit_item_run_result",
        table_name="device_audit_item",
        schema="ctip",
    )
    op.drop_table("device_audit_item", schema="ctip")
    op.drop_index(
        "idx_device_audit_run_status_created",
        table_name="device_audit_run",
        schema="ctip",
    )
    op.drop_table("device_audit_run", schema="ctip")
