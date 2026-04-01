"""add flow pricing and business status

Revision ID: 9f6a8b7c2d41
Revises: c8d3c6f8f3a1
Create Date: 2026-03-17 11:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9f6a8b7c2d41"
down_revision: str | Sequence[str] | None = "c8d3c6f8f3a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "form_workflow_case",
        sa.Column(
            "business_status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'DRAFT'"),
        ),
        schema="ctip",
    )
    op.create_check_constraint(
        "form_workflow_case_business_status_check",
        "form_workflow_case",
        "business_status in ('DRAFT','PENDING_APPROVAL','APPROVED','ZEROWKA','REJECTED')",
        schema="ctip",
    )
    op.add_column(
        "form_workflow_device",
        sa.Column("price_net", sa.Text(), nullable=True),
        schema="ctip",
    )
    op.add_column(
        "form_workflow_device",
        sa.Column("price_gross", sa.Text(), nullable=True),
        schema="ctip",
    )


def downgrade() -> None:
    op.drop_column("form_workflow_device", "price_gross", schema="ctip")
    op.drop_column("form_workflow_device", "price_net", schema="ctip")
    op.drop_constraint(
        "form_workflow_case_business_status_check",
        "form_workflow_case",
        type_="check",
        schema="ctip",
    )
    op.drop_column("form_workflow_case", "business_status", schema="ctip")
