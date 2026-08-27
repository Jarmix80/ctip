"""Dodanie wyboru rozliczenia wysyłki przez fakturę.

Revision ID: b5c6d7e8f901
Revises: a4b5c6d7e8f9
Create Date: 2026-08-24 12:25:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b5c6d7e8f901"
down_revision: str | Sequence[str] | None = "a4b5c6d7e8f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "ctip"


def upgrade() -> None:
    """Dodaje jawną decyzję operatora o wystawieniu faktury."""
    op.add_column(
        "shipping_case",
        sa.Column(
            "invoice_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Usuwa decyzję o wystawieniu faktury ze spraw wysyłkowych."""
    op.drop_column("shipping_case", "invoice_required", schema=SCHEMA)
