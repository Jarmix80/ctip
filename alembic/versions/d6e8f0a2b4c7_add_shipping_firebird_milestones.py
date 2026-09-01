"""Dodanie znaczników synchronizacji przesyłki z Menadżerem Serwisu.

Revision ID: d6e8f0a2b4c7
Revises: a1c3e5f7b9d2
Create Date: 2026-09-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d6e8f0a2b4c7"
down_revision: str | Sequence[str] | None = "a1c3e5f7b9d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Dodaje idempotentne znaczniki zapisu etapów DPD do Firebirda."""
    columns = (
        sa.Column("firebird_label_metadata_synced_at", sa.DateTime(timezone=True)),
        sa.Column("firebird_pickup_event_key", sa.Text()),
        sa.Column("firebird_pickup_synced_at", sa.DateTime(timezone=True)),
        sa.Column("firebird_delivery_event_key", sa.Text()),
        sa.Column("firebird_delivery_synced_at", sa.DateTime(timezone=True)),
        sa.Column("firebird_description_event_key", sa.Text()),
        sa.Column("firebird_description_synced_at", sa.DateTime(timezone=True)),
        sa.Column("firebird_milestone_error", sa.Text()),
    )
    for column in columns:
        op.add_column("shipping_shipment", column, schema="ctip")


def downgrade() -> None:
    """Usuwa znaczniki synchronizacji etapów DPD z Firebirdem."""
    for column_name in (
        "firebird_milestone_error",
        "firebird_description_synced_at",
        "firebird_description_event_key",
        "firebird_delivery_synced_at",
        "firebird_delivery_event_key",
        "firebird_pickup_synced_at",
        "firebird_pickup_event_key",
        "firebird_label_metadata_synced_at",
    ):
        op.drop_column("shipping_shipment", column_name, schema="ctip")
