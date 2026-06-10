"""Dodaj status i bucket Ksero-Partner do workflow formularzy.

Revision ID: 6c0f2d9f1a4e
Revises: e7f0a1b2c3d4
Create Date: 2026-06-10 00:00:00.000000
"""

from alembic import op

revision = "6c0f2d9f1a4e"
down_revision = "e7f0a1b2c3d4"
branch_labels = None
depends_on = None

SCHEMA = "ctip"


def upgrade() -> None:
    """Aktualizuje ograniczenia biznesowe workflow o nowy status i bucket."""
    op.drop_constraint(
        "form_request_archive_bucket_check",
        "form_request",
        type_="check",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "form_request_archive_bucket_check",
        "form_request",
        "archive_bucket is null or archive_bucket in ('accepted','rejected','unfilled','ksero_partner')",
        schema=SCHEMA,
    )

    op.drop_constraint(
        "form_workflow_case_business_status_check",
        "form_workflow_case",
        type_="check",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "form_workflow_case_business_status_check",
        "form_workflow_case",
        "business_status in ("
        "'DRAFT','PENDING_APPROVAL','APPROVED','ZEROWKA','REJECTED',"
        "'WAITING_SIGNATURE','APPROVED_ORDER','REJECTED_GRENKE','RENTAL_WITHOUT_GRENKE'"
        ")",
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Cofa rozszerzenie workflow o status i bucket Ksero-Partner."""
    op.drop_constraint(
        "form_request_archive_bucket_check",
        "form_request",
        type_="check",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "form_request_archive_bucket_check",
        "form_request",
        "archive_bucket is null or archive_bucket in ('accepted','rejected','unfilled')",
        schema=SCHEMA,
    )

    op.drop_constraint(
        "form_workflow_case_business_status_check",
        "form_workflow_case",
        type_="check",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "form_workflow_case_business_status_check",
        "form_workflow_case",
        "business_status in ('DRAFT','PENDING_APPROVAL','APPROVED','ZEROWKA','REJECTED',"
        "'WAITING_SIGNATURE','APPROVED_ORDER','REJECTED_GRENKE')",
        schema=SCHEMA,
    )
