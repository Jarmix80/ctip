"""Dodanie preferencji wyglądu modułu wysyłek.

Revision ID: a1c3e5f7b9d2
Revises: f7b2d4e6a810
Create Date: 2026-09-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1c3e5f7b9d2"
down_revision: str | Sequence[str] | None = "f7b2d4e6a810"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Dodaje trwały wybór wyglądu Shipping do konta użytkownika."""
    op.add_column(
        "admin_user",
        sa.Column(
            "shipping_layout",
            sa.Text(),
            server_default="v2",
            nullable=False,
        ),
        schema="ctip",
    )
    op.create_check_constraint(
        "admin_user_shipping_layout_check",
        "admin_user",
        "shipping_layout in ('v2','legacy')",
        schema="ctip",
    )


def downgrade() -> None:
    """Usuwa preferencję wyglądu Shipping z konta użytkownika."""
    op.drop_constraint(
        "admin_user_shipping_layout_check",
        "admin_user",
        schema="ctip",
        type_="check",
    )
    op.drop_column("admin_user", "shipping_layout", schema="ctip")
