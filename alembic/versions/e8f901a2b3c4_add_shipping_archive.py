"""Dodanie niezmiennego Archiwum wysyłek i dokumentów.

Revision ID: e8f901a2b3c4
Revises: d7e8f901a2b3
Create Date: 2026-08-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e8f901a2b3c4"
down_revision: str | Sequence[str] | None = "d7e8f901a2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "ctip"


def upgrade() -> None:
    """Dodaje snapshot, operatora zamknięcia i indeksowaną treść wyszukiwarki."""
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.add_column(
        "shipping_shipment",
        sa.Column("closed_by", sa.Integer(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "shipping_shipment",
        sa.Column("archive_snapshot", sa.JSON(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "shipping_shipment",
        sa.Column("archive_search_text", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_shipping_shipment_closed_by",
        "shipping_shipment",
        "admin_user",
        ["closed_by"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )

    op.execute(
        """
        UPDATE ctip.shipping_shipment AS shipment
        SET closed_by = (
            SELECT event.created_by
            FROM ctip.shipping_event AS event
            WHERE event.shipment_id = shipment.id
              AND event.event_type = 'courier_handover'
            ORDER BY event.created_at DESC, event.id DESC
            LIMIT 1
        )
        WHERE shipment.status = 'closed'
          AND shipment.closed_by IS NULL
        """
    )
    op.execute(
        """
        UPDATE ctip.shipping_shipment AS shipment
        SET archive_snapshot = jsonb_build_object(
            'version', 1,
            'archived_at', shipment.closed_at,
            'order', jsonb_build_object(
                'order_table_id', shipping_case.firebird_order_table_id,
                'order_id', shipping_case.firebird_order_id,
                'order_year', shipping_case.firebird_order_year,
                'order_number', concat(
                    shipping_case.firebird_order_id,
                    '/',
                    shipping_case.firebird_order_year
                ),
                'order_date', shipping_case.source_snapshot::jsonb -> 'order_date',
                'order_kind', shipping_case.order_kind,
                'invoice_required', shipping_case.invoice_required,
                'source', CASE
                    WHEN COALESCE(
                        (shipping_case.source_snapshot::jsonb ->> 'created_from_mobile_app')::boolean,
                        false
                    ) THEN 'mobile'
                    ELSE 'manual'
                END,
                'problem', shipping_case.source_snapshot::jsonb -> 'problem',
                'source_snapshot', shipping_case.source_snapshot::jsonb
            ),
            'recipient', shipping_case.address_snapshot::jsonb,
            'device', jsonb_build_object(
                'machine_id', shipping_case.firebird_machine_id,
                'model_id', shipping_case.firebird_model_id,
                'brand', COALESCE(
                    shipping_case.source_snapshot::jsonb -> 'device_brand',
                    shipping_case.source_snapshot::jsonb -> 'machine_brand'
                ),
                'model', COALESCE(
                    shipping_case.source_snapshot::jsonb -> 'device_model',
                    shipping_case.source_snapshot::jsonb -> 'machine_model'
                ),
                'serial', shipping_case.source_snapshot::jsonb -> 'device_serial',
                'asset_number', shipping_case.source_snapshot::jsonb -> 'device_asset_number',
                'location', shipping_case.location_text_snapshot
            ),
            'shipment', jsonb_build_object(
                'id', shipment.id,
                'provider', shipment.provider,
                'provider_mode', shipment.provider_mode,
                'provider_shipment_id', shipment.provider_shipment_id,
                'tracking_number', shipment.tracking_number,
                'weight_kg', shipping_case.weight_kg,
                'label_available', shipment.label_content IS NOT NULL,
                'created_at', shipment.created_at,
                'handed_over_at', shipment.handed_over_at,
                'closed_at', shipment.closed_at,
                'consolidation', shipment.provider_request::jsonb -> 'ctip_consolidation'
            ),
            'documents', jsonb_build_object(
                'mode', CASE
                    WHEN shipping_case.invoice_required THEN 'invoice'
                    WHEN lower(trim(COALESCE(shipping_case.order_kind, ''))) = 'umowa' THEN 'rw'
                    ELSE 'wz'
                END,
                'rw', jsonb_build_object(
                    'id', shipment.firebird_rw_id,
                    'number', shipment.firebird_rw_number
                ),
                'wz', jsonb_build_object(
                    'id', shipment.firebird_wz_id,
                    'number', shipment.firebird_wz_number
                ),
                'invoice', jsonb_build_object(
                    'id', shipment.firebird_invoice_id,
                    'number', shipment.firebird_invoice_number
                )
            ),
            'items', COALESCE((
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'id', item.id,
                        'warehouse_item_id', item.firebird_warehouse_item_id,
                        'warehouse_id', item.warehouse_id,
                        'index', item.item_index,
                        'name', item.item_name,
                        'unit', item.unit,
                        'quantity', item.quantity,
                        'price_net', item.price_net,
                        'catalog_price_net', item.catalog_price_net,
                        'purchase_price_net', item.purchase_price_net,
                        'price_source', item.price_source,
                        'vat_rate', item.vat_rate,
                        'negative_stock_authorized', item.allow_negative_stock,
                        'firebird_position_id', item.firebird_position_id
                    ) ORDER BY item.id
                )
                FROM ctip.shipping_item AS item
                WHERE item.shipping_case_id = shipping_case.id
            ), '[]'::jsonb),
            'operators', jsonb_build_object(
                'reviewed', CASE
                    WHEN shipping_case.reviewed_by IS NULL THEN NULL
                    ELSE jsonb_build_object(
                        'id', shipping_case.reviewed_by,
                        'name', (
                            SELECT COALESCE(
                                NULLIF(trim(concat_ws(' ', admin.first_name, admin.last_name)), ''),
                                admin.email
                            )
                            FROM ctip.admin_user AS admin
                            WHERE admin.id = shipping_case.reviewed_by
                        )
                    )
                END,
                'label_created', CASE
                    WHEN shipment.created_by IS NULL THEN NULL
                    ELSE jsonb_build_object(
                        'id', shipment.created_by,
                        'name', (
                            SELECT COALESCE(
                                NULLIF(trim(concat_ws(' ', admin.first_name, admin.last_name)), ''),
                                admin.email
                            )
                            FROM ctip.admin_user AS admin
                            WHERE admin.id = shipment.created_by
                        )
                    )
                END,
                'closed', CASE
                    WHEN shipment.closed_by IS NULL THEN NULL
                    ELSE jsonb_build_object(
                        'id', shipment.closed_by,
                        'name', (
                            SELECT COALESCE(
                                NULLIF(trim(concat_ws(' ', admin.first_name, admin.last_name)), ''),
                                admin.email
                            )
                            FROM ctip.admin_user AS admin
                            WHERE admin.id = shipment.closed_by
                        )
                    )
                END
            )
        )::json
        FROM ctip.shipping_case AS shipping_case
        WHERE shipping_case.id = shipment.shipping_case_id
          AND shipment.status = 'closed'
        """
    )
    op.execute(
        """
        UPDATE ctip.shipping_shipment
        SET archive_search_text = trim(
            regexp_replace(
                translate(
                    lower(COALESCE(archive_snapshot::text, '')),
                    'ąćęłńóśźż',
                    'acelnoszz'
                ),
                '[^a-z0-9]+',
                ' ',
                'g'
            )
        )
        WHERE status = 'closed'
        """
    )

    op.create_index(
        "idx_shipping_shipment_archive_closed",
        "shipping_shipment",
        ["status", sa.text("closed_at DESC"), sa.text("id DESC")],
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'closed'"),
    )
    op.create_index(
        "idx_shipping_shipment_archive_operator",
        "shipping_shipment",
        ["closed_by", sa.text("closed_at DESC")],
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'closed'"),
    )
    op.execute(
        """
        CREATE INDEX idx_shipping_shipment_archive_search_trgm
        ON ctip.shipping_shipment
        USING gin (archive_search_text gin_trgm_ops)
        WHERE status = 'closed'
        """
    )


def downgrade() -> None:
    """Usuwa pola Archiwum bez usuwania współdzielonego rozszerzenia pg_trgm."""
    op.drop_index(
        "idx_shipping_shipment_archive_search_trgm",
        table_name="shipping_shipment",
        schema=SCHEMA,
    )
    op.drop_index(
        "idx_shipping_shipment_archive_operator",
        table_name="shipping_shipment",
        schema=SCHEMA,
    )
    op.drop_index(
        "idx_shipping_shipment_archive_closed",
        table_name="shipping_shipment",
        schema=SCHEMA,
    )
    op.drop_constraint(
        "fk_shipping_shipment_closed_by",
        "shipping_shipment",
        schema=SCHEMA,
        type_="foreignkey",
    )
    op.drop_column("shipping_shipment", "archive_search_text", schema=SCHEMA)
    op.drop_column("shipping_shipment", "archive_snapshot", schema=SCHEMA)
    op.drop_column("shipping_shipment", "closed_by", schema=SCHEMA)
