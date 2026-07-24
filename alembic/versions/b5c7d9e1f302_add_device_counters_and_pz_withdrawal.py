"""Dodanie liczników urządzeń i bezpiecznego wycofania PZ.

Revision ID: b5c7d9e1f302
Revises: 9d4b6f2a1c80
Create Date: 2026-07-24 16:30:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b5c7d9e1f302"
down_revision: str | Sequence[str] | None = "9d4b6f2a1c80"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Dodaje historię liczników, stan wycofania i uprawnienie operatora."""
    op.add_column(
        "workflow_sheet_status_cache",
        sa.Column("counter_scan", sa.Text(), nullable=True),
        schema="ctip",
    )
    op.add_column(
        "admin_user",
        sa.Column(
            "can_withdraw_device_pz",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        schema="ctip",
    )
    op.drop_constraint(
        "device_intake_operation_status_check",
        "device_intake_operation",
        schema="ctip",
        type_="check",
    )
    op.create_check_constraint(
        "device_intake_operation_status_check",
        "device_intake_operation",
        "status in ('processing','completed','failed','reconcile_required','withdrawn')",
        schema="ctip",
    )
    op.add_column(
        "device_intake_operation",
        sa.Column("withdrawn_by", sa.Integer(), nullable=True),
        schema="ctip",
    )
    op.add_column(
        "device_intake_operation",
        sa.Column("withdrawal_reason", sa.Text(), nullable=True),
        schema="ctip",
    )
    op.add_column(
        "device_intake_operation",
        sa.Column("withdrawal_preview", sa.JSON(), nullable=True),
        schema="ctip",
    )
    op.add_column(
        "device_intake_operation",
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        schema="ctip",
    )
    op.create_foreign_key(
        "device_intake_operation_withdrawn_by_fkey",
        "device_intake_operation",
        "admin_user",
        ["withdrawn_by"],
        ["id"],
        source_schema="ctip",
        referent_schema="ctip",
        ondelete="SET NULL",
    )
    op.add_column(
        "device_inventory_unit",
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        schema="ctip",
    )
    op.add_column(
        "device_inventory_unit",
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        schema="ctip",
    )
    op.create_check_constraint(
        "device_inventory_unit_status_check",
        "device_inventory_unit",
        "status in ('active','withdrawn')",
        schema="ctip",
    )
    op.create_table(
        "device_counter_reading",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("unit_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("reading_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("counter_bw", sa.BigInteger(), nullable=True),
        sa.Column("counter_color", sa.BigInteger(), nullable=True),
        sa.Column("counter_scan", sa.BigInteger(), nullable=True),
        sa.Column(
            "applied_to_current",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column("override_reason", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("source_snapshot", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.timezone("utc", sa.func.now()),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source in ('intake','manual')",
            name="device_counter_reading_source_check",
        ),
        sa.CheckConstraint(
            "counter_bw is not null or counter_color is not null or counter_scan is not null",
            name="device_counter_reading_value_check",
        ),
        sa.CheckConstraint(
            "(counter_bw is null or counter_bw >= 0) and "
            "(counter_color is null or counter_color >= 0) and "
            "(counter_scan is null or counter_scan >= 0)",
            name="device_counter_reading_nonnegative_check",
        ),
        sa.ForeignKeyConstraint(
            ["unit_id"],
            ["ctip.device_inventory_unit.id"],
            name="device_counter_reading_unit_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["ctip.admin_user.id"],
            name="device_counter_reading_created_by_fkey",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="device_counter_reading_pkey"),
        schema="ctip",
    )
    op.create_index(
        "idx_device_counter_reading_unit_date",
        "device_counter_reading",
        ["unit_id", sa.text("reading_at DESC")],
        schema="ctip",
    )
    op.drop_constraint(
        "device_sheet_outbox_operation_type_check",
        "device_sheet_outbox",
        schema="ctip",
        type_="check",
    )
    op.create_check_constraint(
        "device_sheet_outbox_operation_type_check",
        "device_sheet_outbox",
        "operation_type in ('upsert_device','update_note','update_counters','delete_device',"
        "'update_reservation','release_reservation')",
        schema="ctip",
    )


def downgrade() -> None:
    """Usuwa historię liczników i metadane wycofania PZ."""
    op.drop_constraint(
        "device_sheet_outbox_operation_type_check",
        "device_sheet_outbox",
        schema="ctip",
        type_="check",
    )
    op.create_check_constraint(
        "device_sheet_outbox_operation_type_check",
        "device_sheet_outbox",
        "operation_type in ('upsert_device','update_note',"
        "'update_reservation','release_reservation')",
        schema="ctip",
    )
    op.drop_index(
        "idx_device_counter_reading_unit_date",
        table_name="device_counter_reading",
        schema="ctip",
    )
    op.drop_table("device_counter_reading", schema="ctip")
    op.drop_constraint(
        "device_inventory_unit_status_check",
        "device_inventory_unit",
        schema="ctip",
        type_="check",
    )
    op.drop_column("device_inventory_unit", "withdrawn_at", schema="ctip")
    op.drop_column("device_inventory_unit", "status", schema="ctip")
    op.drop_constraint(
        "device_intake_operation_withdrawn_by_fkey",
        "device_intake_operation",
        schema="ctip",
        type_="foreignkey",
    )
    op.drop_column("device_intake_operation", "withdrawn_at", schema="ctip")
    op.drop_column("device_intake_operation", "withdrawal_preview", schema="ctip")
    op.drop_column("device_intake_operation", "withdrawal_reason", schema="ctip")
    op.drop_column("device_intake_operation", "withdrawn_by", schema="ctip")
    op.drop_constraint(
        "device_intake_operation_status_check",
        "device_intake_operation",
        schema="ctip",
        type_="check",
    )
    op.create_check_constraint(
        "device_intake_operation_status_check",
        "device_intake_operation",
        "status in ('processing','completed','failed','reconcile_required')",
        schema="ctip",
    )
    op.drop_column("admin_user", "can_withdraw_device_pz", schema="ctip")
    op.drop_column("workflow_sheet_status_cache", "counter_scan", schema="ctip")
