"""Dodanie edytowalnej treści etykiety DPD do sprawy wysyłkowej.

Revision ID: e4a8c1d9f2b7
Revises: c3d5e7f9a1b2
Create Date: 2026-08-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e4a8c1d9f2b7"
down_revision: str | Sequence[str] | None = "c3d5e7f9a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Dodaje opcjonalny tekst łączący referencję i zawartość etykiety DPD."""
    op.add_column(
        "shipping_case",
        sa.Column("label_text", sa.Text(), nullable=True),
        schema="ctip",
    )


def downgrade() -> None:
    """Usuwa zapisaną treść etykiety ze spraw wysyłkowych."""
    op.drop_column("shipping_case", "label_text", schema="ctip")
