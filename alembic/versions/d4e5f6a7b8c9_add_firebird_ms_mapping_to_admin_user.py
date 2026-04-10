"""Dodanie mapowania użytkownika Menadżera Serwisu do admin_user

Revision ID: d4e5f6a7b8c9
Revises: 7c1d4e2a9b6f
Create Date: 2026-04-10 09:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "7c1d4e2a9b6f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "admin_user",
        sa.Column("firebird_app_user_id", sa.Integer(), nullable=True),
        schema="ctip",
    )
    op.add_column(
        "admin_user",
        sa.Column("firebird_app_user_login", sa.Text(), nullable=True),
        schema="ctip",
    )


def downgrade() -> None:
    op.drop_column("admin_user", "firebird_app_user_login", schema="ctip")
    op.drop_column("admin_user", "firebird_app_user_id", schema="ctip")
