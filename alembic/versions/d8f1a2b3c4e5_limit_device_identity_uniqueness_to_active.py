"""Ograniczenie unikalności tożsamości urządzenia do aktywnych wpisów.

Revision ID: d8f1a2b3c4e5
Revises: c4d8e2f6a1b3
Create Date: 2026-07-31 13:10:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d8f1a2b3c4e5"
down_revision: str | Sequence[str] | None = "c4d8e2f6a1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Pozwala ponownie przyjąć urządzenie zachowane w historii jako wycofane."""
    op.drop_constraint(
        "uq_device_inventory_unit_serial_normalized",
        "device_inventory_unit",
        schema="ctip",
        type_="unique",
    )
    op.drop_constraint(
        "uq_device_inventory_unit_ewidencja_normalized",
        "device_inventory_unit",
        schema="ctip",
        type_="unique",
    )
    op.create_index(
        "uq_device_inventory_unit_serial_normalized",
        "device_inventory_unit",
        ["serial_normalized"],
        unique=True,
        schema="ctip",
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "uq_device_inventory_unit_ewidencja_normalized",
        "device_inventory_unit",
        ["ewidencja_normalized"],
        unique=True,
        schema="ctip",
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    """Przywraca globalną unikalność serialu i numeru KP."""
    op.drop_index(
        "uq_device_inventory_unit_ewidencja_normalized",
        table_name="device_inventory_unit",
        schema="ctip",
    )
    op.drop_index(
        "uq_device_inventory_unit_serial_normalized",
        table_name="device_inventory_unit",
        schema="ctip",
    )
    op.create_unique_constraint(
        "uq_device_inventory_unit_serial_normalized",
        "device_inventory_unit",
        ["serial_normalized"],
        schema="ctip",
    )
    op.create_unique_constraint(
        "uq_device_inventory_unit_ewidencja_normalized",
        "device_inventory_unit",
        ["ewidencja_normalized"],
        schema="ctip",
    )
