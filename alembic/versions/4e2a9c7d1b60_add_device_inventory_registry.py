"""Dodanie rejestru przyjęć i obsługi magazynowej urządzeń.

Revision ID: 4e2a9c7d1b60
Revises: 8a4d1f7c2b90
Create Date: 2026-07-23 10:30:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "4e2a9c7d1b60"
down_revision: str | Sequence[str] | None = "8a4d1f7c2b90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Rozszerza schemat CTIP o rejestr urządzeń i kolejkę Google Sheets."""
    op.add_column(
        "workflow_sheet_status_cache",
        sa.Column("producer", sa.Text(), nullable=True),
        schema="ctip",
    )
    op.add_column(
        "workflow_sheet_status_cache",
        sa.Column("model", sa.Text(), nullable=True),
        schema="ctip",
    )
    op.add_column(
        "workflow_sheet_status_cache",
        sa.Column("serial", sa.Text(), nullable=True),
        schema="ctip",
    )
    op.add_column(
        "workflow_sheet_status_cache",
        sa.Column("sheet_notes", sa.Text(), nullable=True),
        schema="ctip",
    )
    op.add_column(
        "workflow_sheet_status_cache",
        sa.Column("reservation_status", sa.Text(), nullable=True),
        schema="ctip",
    )
    op.add_column(
        "workflow_sheet_status_cache",
        sa.Column("reservation_until", sa.Date(), nullable=True),
        schema="ctip",
    )
    op.add_column(
        "workflow_sheet_status_cache",
        sa.Column("price", sa.Text(), nullable=True),
        schema="ctip",
    )
    op.add_column(
        "workflow_sheet_status_cache",
        sa.Column("ms_id_maszyna", sa.Integer(), nullable=True),
        schema="ctip",
    )

    op.create_table(
        "device_intake_operation",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("supplier_firebird_id", sa.Integer(), nullable=False),
        sa.Column("external_document", sa.Text(), nullable=True),
        sa.Column("exception_reason", sa.Text(), nullable=True),
        sa.Column("firebird_pz_id", sa.Integer(), nullable=True),
        sa.Column("firebird_pz_number", sa.Text(), nullable=True),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("result_snapshot", sa.JSON(), nullable=True),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status in ('processing','completed','failed','reconcile_required')",
            name="device_intake_operation_status_check",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["ctip.admin_user.id"],
            name="device_intake_operation_created_by_fkey",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="device_intake_operation_pkey"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_device_intake_operation_idempotency_key",
        ),
        schema="ctip",
    )

    op.create_table(
        "device_inventory_unit",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("operation_id", sa.Integer(), nullable=True),
        sa.Column(
            "source_type",
            sa.Text(),
            server_default="firebird_magazyn_28",
            nullable=False,
        ),
        sa.Column("source_row", sa.Integer(), nullable=False),
        sa.Column("firebird_pz_id", sa.Integer(), nullable=True),
        sa.Column("firebird_zakpozycja_id", sa.Integer(), nullable=True),
        sa.Column("firebird_machine_id", sa.Integer(), nullable=True),
        sa.Column("firebird_machine_table_id", sa.Integer(), nullable=True),
        sa.Column("firebird_model_id", sa.Integer(), nullable=True),
        sa.Column("firebird_supplier_id", sa.Integer(), nullable=True),
        sa.Column("serial", sa.Text(), nullable=False),
        sa.Column("serial_normalized", sa.Text(), nullable=False),
        sa.Column("ewidencja", sa.Text(), nullable=False),
        sa.Column("ewidencja_normalized", sa.Text(), nullable=False),
        sa.Column("purchase_price_net", sa.Numeric(18, 4), nullable=True),
        sa.Column("sheet_row", sa.Integer(), nullable=True),
        sa.Column("sheet_sync_status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("sheet_sync_error", sa.Text(), nullable=True),
        sa.Column("snapshot", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_type = 'firebird_magazyn_28'",
            name="device_inventory_unit_source_type_check",
        ),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["ctip.device_intake_operation.id"],
            name="device_inventory_unit_operation_id_fkey",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="device_inventory_unit_pkey"),
        sa.UniqueConstraint(
            "source_type",
            "source_row",
            name="uq_device_inventory_unit_source",
        ),
        sa.UniqueConstraint(
            "firebird_zakpozycja_id",
            name="uq_device_inventory_unit_zakpozycja",
        ),
        sa.UniqueConstraint(
            "firebird_machine_table_id",
            name="uq_device_inventory_unit_machine_table",
        ),
        sa.UniqueConstraint(
            "serial_normalized",
            name="uq_device_inventory_unit_serial_normalized",
        ),
        sa.UniqueConstraint(
            "ewidencja_normalized",
            name="uq_device_inventory_unit_ewidencja_normalized",
        ),
        schema="ctip",
    )

    op.create_table(
        "device_inventory_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("unit_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["unit_id"],
            ["ctip.device_inventory_unit.id"],
            name="device_inventory_event_unit_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["ctip.admin_user.id"],
            name="device_inventory_event_created_by_fkey",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="device_inventory_event_pkey"),
        schema="ctip",
    )
    op.create_index(
        "idx_device_inventory_event_unit_created",
        "device_inventory_event",
        ["unit_id", sa.text("created_at DESC")],
        schema="ctip",
    )

    op.create_table(
        "device_manual_reservation",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("unit_id", sa.Integer(), nullable=False),
        sa.Column("reserved_for", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("released_by", sa.Integer(), nullable=True),
        sa.Column("release_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["unit_id"],
            ["ctip.device_inventory_unit.id"],
            name="device_manual_reservation_unit_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["ctip.admin_user.id"],
            name="device_manual_reservation_created_by_fkey",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["released_by"],
            ["ctip.admin_user.id"],
            name="device_manual_reservation_released_by_fkey",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="device_manual_reservation_pkey"),
        schema="ctip",
    )
    op.create_index(
        "uq_device_manual_reservation_active",
        "device_manual_reservation",
        ["unit_id"],
        unique=True,
        schema="ctip",
        postgresql_where=sa.text("released_at IS NULL"),
    )

    op.create_table(
        "device_sheet_outbox",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("unit_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("operation_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="10", nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "operation_type in ("
            "'upsert_device','update_note','update_reservation','release_reservation'"
            ")",
            name="device_sheet_outbox_operation_type_check",
        ),
        sa.CheckConstraint(
            "status in ('pending','processing','completed','failed')",
            name="device_sheet_outbox_status_check",
        ),
        sa.ForeignKeyConstraint(
            ["unit_id"],
            ["ctip.device_inventory_unit.id"],
            name="device_sheet_outbox_unit_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="device_sheet_outbox_pkey"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_device_sheet_outbox_idempotency_key",
        ),
        schema="ctip",
    )
    op.create_index(
        "idx_device_sheet_outbox_pending",
        "device_sheet_outbox",
        ["status", "next_attempt_at"],
        unique=False,
        schema="ctip",
    )


def downgrade() -> None:
    """Usuwa rejestr urządzeń i rozszerzenia cache arkusza."""
    op.drop_index(
        "idx_device_sheet_outbox_pending",
        table_name="device_sheet_outbox",
        schema="ctip",
    )
    op.drop_table("device_sheet_outbox", schema="ctip")
    op.drop_index(
        "uq_device_manual_reservation_active",
        table_name="device_manual_reservation",
        schema="ctip",
    )
    op.drop_table("device_manual_reservation", schema="ctip")
    op.drop_index(
        "idx_device_inventory_event_unit_created",
        table_name="device_inventory_event",
        schema="ctip",
    )
    op.drop_table("device_inventory_event", schema="ctip")
    op.drop_table("device_inventory_unit", schema="ctip")
    op.drop_table("device_intake_operation", schema="ctip")

    for column_name in (
        "ms_id_maszyna",
        "price",
        "reservation_until",
        "reservation_status",
        "sheet_notes",
        "serial",
        "model",
        "producer",
    ):
        op.drop_column(
            "workflow_sheet_status_cache",
            column_name,
            schema="ctip",
        )
