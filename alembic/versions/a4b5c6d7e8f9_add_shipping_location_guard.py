"""Dodanie ochrony przed zmianą lokalizacji urządzenia w wysyłkach.

Revision ID: a4b5c6d7e8f9
Revises: f1a2b3c4d5e6
Create Date: 2026-08-05 13:15:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a4b5c6d7e8f9"
down_revision: str | Sequence[str] | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "ctip"


def upgrade() -> None:
    """Dodaje snapshot i fingerprint lokalizacji do adresów oraz spraw."""
    for table in ("shipping_address", "shipping_case"):
        op.add_column(table, sa.Column("location_source", sa.Text(), nullable=True), schema=SCHEMA)
        op.add_column(
            table,
            sa.Column("location_text_snapshot", sa.Text(), nullable=True),
            schema=SCHEMA,
        )
        op.add_column(
            table,
            sa.Column("location_fingerprint", sa.Text(), nullable=True),
            schema=SCHEMA,
        )
    op.create_index(
        "idx_shipping_address_machine_location",
        "shipping_address",
        ["firebird_machine_id", "location_fingerprint", "updated_at"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Usuwa pola ochrony lokalizacji z modułu wysyłek."""
    op.drop_index(
        "idx_shipping_address_machine_location",
        table_name="shipping_address",
        schema=SCHEMA,
    )
    for table in ("shipping_case", "shipping_address"):
        op.drop_column(table, "location_fingerprint", schema=SCHEMA)
        op.drop_column(table, "location_text_snapshot", schema=SCHEMA)
        op.drop_column(table, "location_source", schema=SCHEMA)
