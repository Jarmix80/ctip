"""Scal heady migracji dostaw i asystenta.

Revision ID: 3f1a2b4c5d6e
Revises: 9b7c6d5e4f3a, e7f0a1b2c3d4
Create Date: 2026-05-20 16:40:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "3f1a2b4c5d6e"
down_revision: str | Sequence[str] | None = ("9b7c6d5e4f3a", "e7f0a1b2c3d4")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Scala niezależne gałęzie migracji bez dodatkowych zmian schematu."""


def downgrade() -> None:
    """Nie wykonuje zmian schematu przy cofnięciu samej rewizji scalającej."""
