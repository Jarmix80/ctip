"""Dodanie liczników urządzeń do lokalnego cache arkusza.

Revision ID: 7c91e2f4a6b8
Revises: 4e2a9c7d1b60
Create Date: 2026-07-23 16:30:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "7c91e2f4a6b8"
down_revision: str | Sequence[str] | None = "4e2a9c7d1b60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Dodaje liczniki B/W i kolor do cache arkusza urządzeń."""
    op.add_column(
        "workflow_sheet_status_cache",
        sa.Column("counter_bw", sa.Text(), nullable=True),
        schema="ctip",
    )
    op.add_column(
        "workflow_sheet_status_cache",
        sa.Column("counter_color", sa.Text(), nullable=True),
        schema="ctip",
    )


def downgrade() -> None:
    """Usuwa liczniki z cache arkusza urządzeń."""
    op.drop_column(
        "workflow_sheet_status_cache",
        "counter_color",
        schema="ctip",
    )
    op.drop_column(
        "workflow_sheet_status_cache",
        "counter_bw",
        schema="ctip",
    )
