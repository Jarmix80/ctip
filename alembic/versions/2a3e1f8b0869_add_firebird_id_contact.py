"""add firebird id column to contact

Revision ID: 2a3e1f8b0869
Revises: fa1d0aa9a3c2
Create Date: 2025-10-25 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2a3e1f8b0869"
down_revision: str | Sequence[str] | None = "fa1d0aa9a3c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("contact", sa.Column("firebird_id", sa.Text(), nullable=True), schema="ctip")
    op.create_index(
        "idx_contact_firebird_id",
        "contact",
        ["firebird_id"],
        unique=False,
        schema="ctip",
    )


def downgrade() -> None:
    op.drop_index("idx_contact_firebird_id", table_name="contact", schema="ctip")
    op.drop_column("contact", "firebird_id", schema="ctip")
