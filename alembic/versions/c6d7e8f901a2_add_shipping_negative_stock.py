"""Dodanie jawnej zgody na ujemny stan pozycji wysyłki.

Revision ID: c6d7e8f901a2
Revises: b5c6d7e8f901
Create Date: 2026-08-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c6d7e8f901a2"
down_revision: str | Sequence[str] | None = "b5c6d7e8f901"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "ctip"


def upgrade() -> None:
    """Dodaje zgodę operatora zapisywaną osobno przy każdej pozycji."""
    op.add_column(
        "shipping_item",
        sa.Column(
            "allow_negative_stock",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Usuwa zgodę na ujemny stan z pozycji wysyłki."""
    op.drop_column("shipping_item", "allow_negative_stock", schema=SCHEMA)
