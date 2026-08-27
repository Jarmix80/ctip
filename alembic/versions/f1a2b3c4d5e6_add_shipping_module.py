"""Dodanie modułu automatyzacji wysyłek DPD.

Revision ID: f1a2b3c4d5e6
Revises: f3a7c9e2d610
Create Date: 2026-07-31 16:30:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "f3a7c9e2d610"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "ctip"


def _utc_now() -> sa.TextClause:
    return sa.text("timezone('utc', now())")


def _upgrade_existing_prototype() -> bool:
    """Adoptuje lokalny prototyp tabel wysyłkowych bez utraty zapisanych danych."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "shipping_address" not in inspector.get_table_names(schema=SCHEMA):
        return False

    table = "shipping_consumable_compatibility"
    existing_columns = {item["name"] for item in inspector.get_columns(table, schema=SCHEMA)}

    def add_column(column: sa.Column) -> None:
        if column.name not in existing_columns:
            op.add_column(table, column, schema=SCHEMA)

    add_column(sa.Column("item_kind", sa.Text(), nullable=True))
    add_column(
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'confirmed'"))
    )
    add_column(sa.Column("confidence", sa.Text(), nullable=True))
    add_column(
        sa.Column(
            "evidence",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        )
    )
    add_column(sa.Column("source_hash", sa.Text(), nullable=True))
    add_column(
        sa.Column(
            "first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=_utc_now()
        )
    )
    add_column(
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=_utc_now()
        )
    )
    add_column(sa.Column("reviewed_by", sa.Integer(), nullable=True))
    add_column(sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    add_column(sa.Column("review_note", sa.Text(), nullable=True))
    add_column(
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_utc_now()
        )
    )
    op.alter_column(table, "status", server_default=sa.text("'suggested'"), schema=SCHEMA)

    existing_foreign_keys = {
        item["name"] for item in inspector.get_foreign_keys(table, schema=SCHEMA)
    }
    if "fk_shipping_compatibility_reviewed_by" not in existing_foreign_keys:
        op.create_foreign_key(
            "fk_shipping_compatibility_reviewed_by",
            table,
            "admin_user",
            ["reviewed_by"],
            ["id"],
            source_schema=SCHEMA,
            referent_schema=SCHEMA,
            ondelete="SET NULL",
        )

    existing_checks = {
        item["name"] for item in inspector.get_check_constraints(table, schema=SCHEMA)
    }
    if "shipping_compatibility_status_check" not in existing_checks:
        op.create_check_constraint(
            "shipping_compatibility_status_check",
            table,
            "status in ('suggested','confirmed','rejected','stale')",
            schema=SCHEMA,
        )
    if "shipping_compatibility_confidence_check" not in existing_checks:
        op.create_check_constraint(
            "shipping_compatibility_confidence_check",
            table,
            "confidence is null or confidence in ('high','medium','low')",
            schema=SCHEMA,
        )

    existing_indexes = {item["name"] for item in inspector.get_indexes(table, schema=SCHEMA)}
    if "idx_shipping_compatibility_status" not in existing_indexes:
        op.create_index(
            "idx_shipping_compatibility_status",
            table,
            ["status", "confidence"],
            schema=SCHEMA,
        )
    return True


def upgrade() -> None:
    """Tworzy dane adresowe, sprawy, przesyłki, zamknięcia dnia i audyt."""
    if _upgrade_existing_prototype():
        return
    op.create_table(
        "shipping_address",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("firebird_client_id", sa.Integer(), nullable=False),
        sa.Column("firebird_machine_id", sa.Integer(), nullable=True),
        sa.Column("location_key", sa.Text(), nullable=False),
        sa.Column("company_name", sa.Text(), nullable=False),
        sa.Column("contact_name", sa.Text(), nullable=True),
        sa.Column("street", sa.Text(), nullable=False),
        sa.Column("postal_code", sa.Text(), nullable=False),
        sa.Column("city", sa.Text(), nullable=False),
        sa.Column("country_code", sa.Text(), nullable=False, server_default="PL"),
        sa.Column("phone", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("verified_by", sa.Integer(), nullable=True),
        sa.Column(
            "verified_at", sa.DateTime(timezone=True), nullable=False, server_default=_utc_now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_utc_now()
        ),
        sa.ForeignKeyConstraint(["verified_by"], ["ctip.admin_user.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "firebird_client_id", "location_key", name="uq_shipping_address_client_location"
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "shipping_consumable_compatibility",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("firebird_model_id", sa.Integer(), nullable=False),
        sa.Column("firebird_warehouse_item_id", sa.Integer(), nullable=False),
        sa.Column("model_label", sa.Text(), nullable=False),
        sa.Column("item_index", sa.Text(), nullable=True),
        sa.Column("item_name", sa.Text(), nullable=False),
        sa.Column("item_kind", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="suggested"),
        sa.Column("confidence", sa.Text(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("source_hash", sa.Text(), nullable=True),
        sa.Column(
            "first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=_utc_now()
        ),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=_utc_now()
        ),
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("confirmed_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=_utc_now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_utc_now()
        ),
        sa.CheckConstraint(
            "status in ('suggested','confirmed','rejected','stale')",
            name="shipping_compatibility_status_check",
        ),
        sa.CheckConstraint(
            "confidence is null or confidence in ('high','medium','low')",
            name="shipping_compatibility_confidence_check",
        ),
        sa.ForeignKeyConstraint(["confirmed_by"], ["ctip.admin_user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["ctip.admin_user.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "firebird_model_id",
            "firebird_warehouse_item_id",
            name="uq_shipping_compatibility_model_item",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_shipping_compatibility_model",
        "shipping_consumable_compatibility",
        ["firebird_model_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_shipping_compatibility_status",
        "shipping_consumable_compatibility",
        ["status", "confidence"],
        schema=SCHEMA,
    )
    op.create_table(
        "shipping_case",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("firebird_order_table_id", sa.Integer(), nullable=False),
        sa.Column("firebird_order_id", sa.Integer(), nullable=False),
        sa.Column("firebird_order_year", sa.Integer(), nullable=False),
        sa.Column("firebird_client_id", sa.Integer(), nullable=False),
        sa.Column("firebird_machine_id", sa.Integer(), nullable=True),
        sa.Column("firebird_model_id", sa.Integer(), nullable=True),
        sa.Column("order_kind", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="review_pending"),
        sa.Column("address_id", sa.Integer(), nullable=True),
        sa.Column("address_snapshot", sa.JSON(), nullable=False),
        sa.Column("source_snapshot", sa.JSON(), nullable=False),
        sa.Column("weight_kg", sa.Numeric(8, 3), nullable=False),
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=_utc_now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_utc_now()
        ),
        sa.CheckConstraint(
            "status in ('review_pending','ready','shipment_created','handed_over','closed',"
            "'manual_billing','reconcile_required')",
            name="shipping_case_status_check",
        ),
        sa.ForeignKeyConstraint(["address_id"], ["ctip.shipping_address.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["ctip.admin_user.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("firebird_order_table_id", name="uq_shipping_case_firebird_order"),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_shipping_case_status_updated",
        "shipping_case",
        ["status", sa.text("updated_at DESC")],
        schema=SCHEMA,
    )
    op.create_table(
        "shipping_item",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("shipping_case_id", sa.Integer(), nullable=False),
        sa.Column("firebird_warehouse_item_id", sa.Integer(), nullable=False),
        sa.Column("warehouse_id", sa.Integer(), nullable=False),
        sa.Column("item_index", sa.Text(), nullable=True),
        sa.Column("item_name", sa.Text(), nullable=False),
        sa.Column("unit", sa.Text(), nullable=False, server_default="szt."),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("price_net", sa.Numeric(18, 4), nullable=False),
        sa.Column("purchase_price_net", sa.Numeric(18, 4), nullable=False),
        sa.Column("vat_rate", sa.Numeric(8, 3), nullable=False),
        sa.Column("firebird_position_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=_utc_now()
        ),
        sa.ForeignKeyConstraint(
            ["shipping_case_id"], ["ctip.shipping_case.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "shipping_case_id",
            "firebird_warehouse_item_id",
            name="uq_shipping_item_case_warehouse",
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "shipping_day_close",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="processing"),
        sa.Column("shipment_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("closed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("closed_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=_utc_now()
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status in ('processing','completed','partial','failed')",
            name="shipping_day_close_status_check",
        ),
        sa.ForeignKeyConstraint(["closed_by"], ["ctip.admin_user.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("business_date", name="uq_shipping_day_close_date"),
        schema=SCHEMA,
    )
    op.create_table(
        "shipping_shipment",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("shipping_case_id", sa.Integer(), nullable=False),
        sa.Column("day_close_id", sa.Integer(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False, server_default="dpd"),
        sa.Column("provider_mode", sa.Text(), nullable=False),
        sa.Column("provider_shipment_id", sa.Text(), nullable=True),
        sa.Column("tracking_number", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="processing"),
        sa.Column("label_content", sa.LargeBinary(), nullable=True),
        sa.Column("label_content_type", sa.Text(), nullable=True),
        sa.Column("label_format", sa.Text(), nullable=True),
        sa.Column("provider_request", sa.JSON(), nullable=False),
        sa.Column("provider_response", sa.JSON(), nullable=True),
        sa.Column("firebird_status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("firebird_error", sa.Text(), nullable=True),
        sa.Column("notification_sms_status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("notification_email_status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("notification_error", sa.Text(), nullable=True),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=_utc_now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_utc_now()
        ),
        sa.Column("handed_over_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status in ('processing','label_ready','handed_over','closed','failed',"
            "'reconcile_required')",
            name="shipping_shipment_status_check",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["ctip.admin_user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["day_close_id"], ["ctip.shipping_day_close.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["shipping_case_id"], ["ctip.shipping_case.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("shipping_case_id", name="uq_shipping_shipment_case"),
        sa.UniqueConstraint("idempotency_key", name="uq_shipping_shipment_idempotency"),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_shipping_shipment_status_created",
        "shipping_shipment",
        ["status", sa.text("created_at DESC")],
        schema=SCHEMA,
    )
    op.create_table(
        "shipping_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("shipping_case_id", sa.Integer(), nullable=False),
        sa.Column("shipment_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=_utc_now()
        ),
        sa.ForeignKeyConstraint(["created_by"], ["ctip.admin_user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["shipment_id"], ["ctip.shipping_shipment.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["shipping_case_id"], ["ctip.shipping_case.id"], ondelete="CASCADE"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_shipping_event_case_created",
        "shipping_event",
        ["shipping_case_id", sa.text("created_at DESC")],
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Usuwa struktury modułu wysyłek."""
    op.drop_table("shipping_event", schema=SCHEMA)
    op.drop_table("shipping_shipment", schema=SCHEMA)
    op.drop_table("shipping_day_close", schema=SCHEMA)
    op.drop_table("shipping_item", schema=SCHEMA)
    op.drop_table("shipping_case", schema=SCHEMA)
    op.drop_table("shipping_consumable_compatibility", schema=SCHEMA)
    op.drop_table("shipping_address", schema=SCHEMA)
