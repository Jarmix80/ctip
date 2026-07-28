"""Scalenie gałęzi urządzeń z gałęzią dostaw i umów.

Revision ID: d6f1a8c3e740
Revises: 4c9a2e7d6f10, c4d8e2f6a1b3
Create Date: 2026-07-27 16:50:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "d6f1a8c3e740"
down_revision: str | Sequence[str] | None = (
    "4c9a2e7d6f10",
    "c4d8e2f6a1b3",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Scala historię migracji bez dodatkowych zmian schematu."""


def downgrade() -> None:
    """Rozdziela wyłącznie historię migracji."""
