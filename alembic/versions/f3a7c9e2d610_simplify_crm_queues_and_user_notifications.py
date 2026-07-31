"""Upraszcza kolejki CRM i dodaje ustawienia powiadomień użytkownika."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f3a7c9e2d610"
down_revision: str | Sequence[str] | None = "a6f3c8d2e910"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "ctip"


def upgrade() -> None:
    op.add_column(
        "admin_user",
        sa.Column(
            "crm_sales_sms_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "admin_user",
        sa.Column(
            "crm_sales_email_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "admin_user",
        sa.Column(
            "crm_operations_sms_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "admin_user",
        sa.Column(
            "crm_operations_email_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "crm_case",
        sa.Column("category", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    op.execute(
        sa.text(
            """
            UPDATE ctip.crm_case
            SET category = CASE queue
                WHEN 'meters' THEN 'meters'
                WHEN 'accounting' THEN 'accounting'
                WHEN 'contracts' THEN 'contracts'
                WHEN 'service_it' THEN 'service'
                WHEN 'sales' THEN 'sales'
                ELSE 'other'
            END
            WHERE category IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE ctip.crm_case
            SET queue = CASE queue
                WHEN 'meters' THEN 'contracts'
                WHEN 'accounting' THEN 'other'
                ELSE queue
            END
            WHERE queue IN ('meters', 'accounting')
            """
        )
    )
    op.alter_column("crm_case", "category", nullable=False, schema=SCHEMA)
    op.drop_constraint("ck_crm_case_queue", "crm_case", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        "ck_crm_case_queue",
        "crm_case",
        "queue in ('sales','service_it','contracts','other')",
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint("ck_crm_case_queue", "crm_case", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        "ck_crm_case_queue",
        "crm_case",
        "queue in ('sales','service_it','accounting','contracts','meters','other')",
        schema=SCHEMA,
    )
    op.drop_column("crm_case", "category", schema=SCHEMA)
    op.drop_column("admin_user", "crm_operations_email_enabled", schema=SCHEMA)
    op.drop_column("admin_user", "crm_operations_sms_enabled", schema=SCHEMA)
    op.drop_column("admin_user", "crm_sales_email_enabled", schema=SCHEMA)
    op.drop_column("admin_user", "crm_sales_sms_enabled", schema=SCHEMA)
