"""Dodanie cen rozliczeniowych i identyfikatorów dokumentów wysyłki.

Revision ID: d7e8f901a2b3
Revises: c6d7e8f901a2
Create Date: 2026-08-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d7e8f901a2b3"
down_revision: str | Sequence[str] | None = "c6d7e8f901a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "ctip"


def upgrade() -> None:
    """Rozszerza snapshot pozycji oraz zapis wynikowych dokumentów MS."""
    op.add_column(
        "shipping_item",
        sa.Column(
            "catalog_price_net",
            sa.Numeric(18, 4),
            server_default="0",
            nullable=False,
        ),
        schema=SCHEMA,
    )
    op.execute("UPDATE ctip.shipping_item SET catalog_price_net = price_net")
    op.add_column(
        "shipping_item",
        sa.Column("price_source", sa.Text(), server_default="sale", nullable=False),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "shipping_item_price_source_check",
        "shipping_item",
        "price_source in ('sale','purchase_fallback','purchase_contract','manual')",
        schema=SCHEMA,
    )
    for column in (
        sa.Column("firebird_rw_id", sa.Integer(), nullable=True),
        sa.Column("firebird_rw_number", sa.Text(), nullable=True),
        sa.Column("firebird_wz_id", sa.Integer(), nullable=True),
        sa.Column("firebird_wz_number", sa.Text(), nullable=True),
        sa.Column("firebird_invoice_id", sa.Integer(), nullable=True),
        sa.Column("firebird_invoice_number", sa.Text(), nullable=True),
    ):
        op.add_column("shipping_shipment", column, schema=SCHEMA)


def downgrade() -> None:
    """Usuwa snapshot źródła ceny oraz identyfikatory dokumentów MS."""
    for column_name in (
        "firebird_invoice_number",
        "firebird_invoice_id",
        "firebird_wz_number",
        "firebird_wz_id",
        "firebird_rw_number",
        "firebird_rw_id",
    ):
        op.drop_column("shipping_shipment", column_name, schema=SCHEMA)
    op.drop_constraint(
        "shipping_item_price_source_check",
        "shipping_item",
        schema=SCHEMA,
        type_="check",
    )
    op.drop_column("shipping_item", "price_source", schema=SCHEMA)
    op.drop_column("shipping_item", "catalog_price_net", schema=SCHEMA)
