"""add ms status to form request

Revision ID: 5f4e9a7b1c2d
Revises: b3f41e2c1a9d
Create Date: 2026-04-09 13:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "5f4e9a7b1c2d"
down_revision: str | Sequence[str] | None = "b3f41e2c1a9d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("form_request", sa.Column("ms_status", sa.Text(), nullable=True), schema="ctip")


def downgrade() -> None:
    op.drop_column("form_request", "ms_status", schema="ctip")
