"""Scala gałęzie migracji wysyłek i katalogu tożsamości.

Revision ID: f7b2d4e6a810
Revises: a6f3c8d2e910, e4a8c1d9f2b7
Create Date: 2026-09-01 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "f7b2d4e6a810"
down_revision: str | Sequence[str] | None = ("a6f3c8d2e910", "e4a8c1d9f2b7")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Scala niezależne gałęzie bez dodatkowych zmian schematu."""


def downgrade() -> None:
    """Nie zmienia schematu przy cofnięciu samej rewizji scalającej."""
