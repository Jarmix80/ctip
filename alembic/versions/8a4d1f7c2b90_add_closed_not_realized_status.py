"""Dodaj status zamkniecia bez realizacji.

Revision ID: 8a4d1f7c2b90
Revises: 6c0f2d9f1a4e
Create Date: 2026-06-11 00:00:00.000000
"""

from alembic import op

revision = "8a4d1f7c2b90"
down_revision = "6c0f2d9f1a4e"
branch_labels = None
depends_on = None

SCHEMA = "ctip"


def upgrade() -> None:
    """Rozszerza ograniczenia workflow o zamkniecie bez realizacji."""
    op.drop_constraint(
        "form_request_archive_bucket_check",
        "form_request",
        type_="check",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "form_request_archive_bucket_check",
        "form_request",
        "archive_bucket is null or archive_bucket in "
        "('accepted','rejected','unfilled','ksero_partner','closed_other')",
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
        "'WAITING_SIGNATURE','APPROVED_ORDER','REJECTED_GRENKE',"
        "'RENTAL_WITHOUT_GRENKE','CLOSED_NOT_REALIZED'"
        ")",
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Cofa status zamkniecia bez realizacji z ograniczen workflow."""
    op.drop_constraint(
        "form_request_archive_bucket_check",
        "form_request",
        type_="check",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "form_request_archive_bucket_check",
        "form_request",
        "archive_bucket is null or archive_bucket in "
        "('accepted','rejected','unfilled','ksero_partner')",
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
