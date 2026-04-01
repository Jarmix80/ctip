"""add flow delivery schedule fields

Revision ID: b3f41e2c1a9d
Revises: 9f6a8b7c2d41
Create Date: 2026-03-18 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3f41e2c1a9d"
down_revision: str | Sequence[str] | None = "9f6a8b7c2d41"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "form_workflow_case",
        sa.Column("delivery_date", sa.Date(), nullable=True),
        schema="ctip",
    )
    op.add_column(
        "form_workflow_case",
        sa.Column("delivery_time_window", sa.Text(), nullable=True),
        schema="ctip",
    )
    op.add_column(
        "form_workflow_case",
        sa.Column("delivery_contact_name", sa.Text(), nullable=True),
        schema="ctip",
    )
    op.add_column(
        "form_workflow_case",
        sa.Column("delivery_contact_phone", sa.Text(), nullable=True),
        schema="ctip",
    )
    op.add_column(
        "form_workflow_case",
        sa.Column("delivery_notes", sa.Text(), nullable=True),
        schema="ctip",
    )


def downgrade() -> None:
    op.drop_column("form_workflow_case", "delivery_notes", schema="ctip")
    op.drop_column("form_workflow_case", "delivery_contact_phone", schema="ctip")
    op.drop_column("form_workflow_case", "delivery_contact_name", schema="ctip")
    op.drop_column("form_workflow_case", "delivery_time_window", schema="ctip")
    op.drop_column("form_workflow_case", "delivery_date", schema="ctip")
