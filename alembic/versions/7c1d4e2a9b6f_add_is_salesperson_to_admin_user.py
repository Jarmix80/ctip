"""add is_salesperson to admin_user

Revision ID: 7c1d4e2a9b6f
Revises: 5f4e9a7b1c2d
Create Date: 2026-04-09 15:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "7c1d4e2a9b6f"
down_revision: str | Sequence[str] | None = "5f4e9a7b1c2d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "admin_user",
        sa.Column("is_salesperson", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="ctip",
    )
    op.alter_column("admin_user", "is_salesperson", server_default=None, schema="ctip")


def downgrade() -> None:
    op.drop_column("admin_user", "is_salesperson", schema="ctip")
