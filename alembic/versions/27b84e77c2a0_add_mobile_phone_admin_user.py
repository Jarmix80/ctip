"""add mobile phone column to admin_user

Revision ID: 27b84e77c2a0
Revises: e0ea7dc108d6
Create Date: 2025-10-22 09:15:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "27b84e77c2a0"
down_revision: str | Sequence[str] | None = "fa1d0aa9a3c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "admin_user",
        sa.Column("mobile_phone", sa.Text(), nullable=True),
        schema="ctip",
    )


def downgrade() -> None:
    op.drop_column("admin_user", "mobile_phone", schema="ctip")
