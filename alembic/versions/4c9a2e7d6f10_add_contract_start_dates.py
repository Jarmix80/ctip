"""Dodaj daty poczatku umow GRENKE i KP.

Revision ID: 4c9a2e7d6f10
Revises: 3f1a2b4c5d6e, 8a4d1f7c2b90
Create Date: 2026-06-19 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "4c9a2e7d6f10"
down_revision: str | Sequence[str] | None = ("3f1a2b4c5d6e", "8a4d1f7c2b90")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "ctip"


def upgrade() -> None:
    """Dodaje jawne daty startu umow i uzupelnia je z danych historycznych."""
    op.add_column(
        "form_workflow_case",
        sa.Column("grenke_contract_start_date", sa.Date(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "form_workflow_case",
        sa.Column("kp_contract_start_date", sa.Date(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "grenke_contract_end",
        sa.Column("grenke_contract_start_date", sa.Date(), nullable=True),
        schema=SCHEMA,
    )

    op.execute(
        """
        UPDATE ctip.form_workflow_case
           SET kp_contract_start_date = delivery_date
         WHERE kp_contract_start_date IS NULL
           AND delivery_date IS NOT NULL
        """
    )
    op.execute(
        """
        WITH approved_history AS (
            SELECT
                form_case.id,
                min((event.value ->> 'changed_at')::timestamptz)::date AS first_approved_at
            FROM ctip.form_workflow_case AS form_case
            CROSS JOIN LATERAL json_array_elements(
                CASE
                    WHEN json_typeof(form_case.status_history) = 'array'
                    THEN form_case.status_history
                    ELSE '[]'::json
                END
            ) AS event(value)
            WHERE event.value ->> 'status' IN ('APPROVED_ORDER', 'APPROVED')
              AND event.value ->> 'changed_at' ~ '^\\d{4}-\\d{2}-\\d{2}'
            GROUP BY form_case.id
        )
        UPDATE ctip.form_workflow_case AS form_case
           SET grenke_contract_start_date = approved_history.first_approved_at
          FROM approved_history
         WHERE form_case.id = approved_history.id
           AND form_case.grenke_contract_start_date IS NULL
        """
    )
    op.execute(
        """
        UPDATE ctip.form_workflow_case
           SET grenke_contract_start_date = status_changed_at::date
         WHERE grenke_contract_start_date IS NULL
           AND business_status IN ('APPROVED_ORDER', 'APPROVED')
           AND status_changed_at IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE ctip.grenke_contract_end AS contract_end
           SET grenke_contract_start_date = form_case.grenke_contract_start_date
          FROM ctip.form_workflow_case AS form_case
         WHERE contract_end.workflow_case_id = form_case.id
           AND contract_end.grenke_contract_start_date IS NULL
           AND form_case.grenke_contract_start_date IS NOT NULL
        """
    )


def downgrade() -> None:
    """Usuwa daty startu umow."""
    op.drop_column("grenke_contract_end", "grenke_contract_start_date", schema=SCHEMA)
    op.drop_column("form_workflow_case", "kp_contract_start_date", schema=SCHEMA)
    op.drop_column("form_workflow_case", "grenke_contract_start_date", schema=SCHEMA)
