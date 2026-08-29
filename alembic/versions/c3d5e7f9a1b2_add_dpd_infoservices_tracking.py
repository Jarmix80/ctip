"""Dodanie śledzenia przesyłek DPD InfoServices.

Revision ID: c3d5e7f9a1b2
Revises: a7c4e2f9b1d3
Create Date: 2026-08-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3d5e7f9a1b2"
down_revision: str | Sequence[str] | None = "a7c4e2f9b1d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "ctip"


def _utc_now() -> sa.TextClause:
    return sa.text("timezone('utc', now())")


def upgrade() -> None:
    """Tworzy addytywny rejestr listów, zdarzeń i synchronizacji DPD."""
    op.create_table(
        "shipping_tracking_parcel",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.Text(), nullable=False, server_default="dpd"),
        sa.Column("waybill", sa.Text(), nullable=False),
        sa.Column("source_channel", sa.Text(), nullable=False),
        sa.Column("latest_business_code", sa.Text(), nullable=True),
        sa.Column("latest_description", sa.Text(), nullable=True),
        sa.Column("latest_event_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latest_depot", sa.Text(), nullable=True),
        sa.Column("latest_depot_name", sa.Text(), nullable=True),
        sa.Column("latest_country", sa.Text(), nullable=True),
        sa.Column("replacement_waybill", sa.Text(), nullable=True),
        sa.Column("status_category", sa.Text(), nullable=False, server_default="other"),
        sa.Column("status_label", sa.Text(), nullable=False, server_default="Inny status DPD"),
        sa.Column("is_terminal", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("requires_attention", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("first_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_synced_at", sa.DateTime(timezone=True), nullable=False, server_default=_utc_now()
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=_utc_now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_utc_now()
        ),
        sa.CheckConstraint(
            "status_category in ("
            "'registered','in_transit','out_for_delivery','pickup_ready','delivered',"
            "'undelivered','redirected','returning','critical','other'"
            ")",
            name="shipping_tracking_parcel_category_check",
        ),
        sa.UniqueConstraint("provider", "waybill", name="uq_shipping_tracking_parcel_waybill"),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_shipping_tracking_parcel_status_event",
        "shipping_tracking_parcel",
        ["status_category", sa.text("latest_event_time DESC")],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_shipping_tracking_parcel_attention_event",
        "shipping_tracking_parcel",
        ["requires_attention", sa.text("latest_event_time DESC")],
        schema=SCHEMA,
    )

    op.create_table(
        "shipping_tracking_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("parcel_id", sa.Integer(), nullable=True),
        sa.Column("source_event_key", sa.Text(), nullable=False),
        sa.Column("waybill", sa.Text(), nullable=True),
        sa.Column("dpd_event_id", sa.Text(), nullable=True),
        sa.Column("object_id", sa.Text(), nullable=True),
        sa.Column("business_code", sa.Text(), nullable=True),
        sa.Column("operation_type", sa.Text(), nullable=False, server_default="INSERT"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("depot", sa.Text(), nullable=True),
        sa.Column("depot_name", sa.Text(), nullable=True),
        sa.Column("country", sa.Text(), nullable=True),
        sa.Column("package_reference", sa.Text(), nullable=True),
        sa.Column("parcel_reference", sa.Text(), nullable=True),
        sa.Column("event_data", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("raw_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("is_cancelled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), nullable=False, server_default=_utc_now()
        ),
        sa.CheckConstraint(
            "operation_type in ('INSERT','CANCEL')",
            name="shipping_tracking_event_operation_check",
        ),
        sa.ForeignKeyConstraint(
            ["parcel_id"],
            ["ctip.shipping_tracking_parcel.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("source_event_key", name="uq_shipping_tracking_event_source_key"),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_shipping_tracking_event_parcel_time",
        "shipping_tracking_event",
        ["parcel_id", sa.text("event_time DESC"), sa.text("id DESC")],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_shipping_tracking_event_object",
        "shipping_tracking_event",
        ["object_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "shipping_tracking_sync_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_channel", sa.Text(), nullable=False),
        sa.Column("trigger_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="processing"),
        sa.Column("fetched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("inserted_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cancelled_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("batch_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confirm_id", sa.Text(), nullable=True),
        sa.Column(
            "acknowledgement_confirmed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.Column("triggered_by", sa.Integer(), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=_utc_now()
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "trigger_type in ('scheduler','manual','backfill')",
            name="shipping_tracking_sync_run_trigger_check",
        ),
        sa.CheckConstraint(
            "status in ('processing','success','partial','failed')",
            name="shipping_tracking_sync_run_status_check",
        ),
        sa.ForeignKeyConstraint(["triggered_by"], ["ctip.admin_user.id"], ondelete="SET NULL"),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_shipping_tracking_sync_started",
        "shipping_tracking_sync_run",
        [sa.text("started_at DESC")],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_shipping_shipment_provider_tracking",
        "shipping_shipment",
        ["provider", "tracking_number"],
        schema=SCHEMA,
    )

    for table in (
        "shipping_tracking_parcel",
        "shipping_tracking_event",
        "shipping_tracking_sync_run",
    ):
        op.execute(f"GRANT SELECT,INSERT,DELETE,UPDATE ON ctip.{table} TO appuser")
        op.execute(f"GRANT ALL ON SEQUENCE ctip.{table}_id_seq TO appuser")


def downgrade() -> None:
    """Usuwa wyłącznie addytywne obiekty śledzenia DPD."""
    op.drop_index(
        "idx_shipping_shipment_provider_tracking",
        table_name="shipping_shipment",
        schema=SCHEMA,
    )
    op.drop_table("shipping_tracking_sync_run", schema=SCHEMA)
    op.drop_table("shipping_tracking_event", schema=SCHEMA)
    op.drop_table("shipping_tracking_parcel", schema=SCHEMA)
