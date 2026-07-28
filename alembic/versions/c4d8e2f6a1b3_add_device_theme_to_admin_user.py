"""Dodanie preferencji kolorystyki modułu urządzeń.

Revision ID: c4d8e2f6a1b3
Revises: b5c7d9e1f302
Create Date: 2026-07-24 19:45:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c4d8e2f6a1b3"
down_revision: str | Sequence[str] | None = "b5c7d9e1f302"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Dodaje trwałą preferencję motywu do konta użytkownika."""
    op.add_column(
        "admin_user",
        sa.Column(
            "device_theme",
            sa.Text(),
            server_default="blue",
            nullable=False,
        ),
        schema="ctip",
    )
    op.create_check_constraint(
        "admin_user_device_theme_check",
        "admin_user",
        "device_theme in ('blue','graphite','mint')",
        schema="ctip",
    )


def downgrade() -> None:
    """Usuwa preferencję motywu z konta użytkownika."""
    op.drop_constraint(
        "admin_user_device_theme_check",
        "admin_user",
        schema="ctip",
        type_="check",
    )
    op.drop_column("admin_user", "device_theme", schema="ctip")
