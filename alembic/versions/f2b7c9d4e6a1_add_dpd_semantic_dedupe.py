"""Dodanie semantycznej deduplikacji zdarzeń DPD InfoServices.

Revision ID: f2b7c9d4e6a1
Revises: d6e8f0a2b4c7
Create Date: 2026-09-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f2b7c9d4e6a1"
down_revision: str | Sequence[str] | None = "d6e8f0a2b4c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Dodaje grupowanie aliasów bez usuwania technicznej historii DPD."""
    op.add_column(
        "shipping_tracking_event",
        sa.Column("semantic_event_key", sa.Text(), nullable=True),
        schema="ctip",
    )
    op.add_column(
        "shipping_tracking_event",
        sa.Column("canonical_event_id", sa.Integer(), nullable=True),
        schema="ctip",
    )
    op.create_foreign_key(
        "fk_shipping_tracking_event_canonical",
        "shipping_tracking_event",
        "shipping_tracking_event",
        ["canonical_event_id"],
        ["id"],
        source_schema="ctip",
        referent_schema="ctip",
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_shipping_tracking_event_semantic",
        "shipping_tracking_event",
        ["semantic_event_key"],
        schema="ctip",
    )
    op.create_index(
        "idx_shipping_tracking_event_canonical",
        "shipping_tracking_event",
        ["canonical_event_id"],
        schema="ctip",
    )
    op.add_column(
        "shipping_tracking_sync_run",
        sa.Column("duplicate_count", sa.Integer(), nullable=False, server_default="0"),
        schema="ctip",
    )


def downgrade() -> None:
    """Usuwa metadane grupowania bez naruszania zdarzeń technicznych."""
    op.drop_column("shipping_tracking_sync_run", "duplicate_count", schema="ctip")
    op.drop_index(
        "idx_shipping_tracking_event_canonical",
        table_name="shipping_tracking_event",
        schema="ctip",
    )
    op.drop_index(
        "idx_shipping_tracking_event_semantic",
        table_name="shipping_tracking_event",
        schema="ctip",
    )
    op.drop_constraint(
        "fk_shipping_tracking_event_canonical",
        "shipping_tracking_event",
        schema="ctip",
        type_="foreignkey",
    )
    op.drop_column("shipping_tracking_event", "canonical_event_id", schema="ctip")
    op.drop_column("shipping_tracking_event", "semantic_event_key", schema="ctip")
