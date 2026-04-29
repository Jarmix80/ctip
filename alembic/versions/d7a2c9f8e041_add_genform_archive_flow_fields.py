"""Dodaj pola archiwizacji i nowych statusów GenForm.

Revision ID: d7a2c9f8e041
Revises: d4e5f6a7b8c9
Create Date: 2026-04-24 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "d7a2c9f8e041"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None
SCHEMA = "ctip"


def upgrade() -> None:
    """Rozszerza workflow formularzy o archiwum, terminy i historię statusu."""
    op.add_column(
        "form_request",
        sa.Column("archive_bucket", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "form_request",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "form_request",
        sa.Column("archive_due_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "form_request_archive_bucket_check",
        "form_request",
        "archive_bucket is null or archive_bucket in ('accepted','rejected','unfilled')",
        schema=SCHEMA,
    )
    op.create_index(
        "ix_form_request_archive_bucket",
        "form_request",
        ["archive_bucket"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_form_request_archive_due_at",
        "form_request",
        ["archive_due_at"],
        unique=False,
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
        "'WAITING_SIGNATURE','APPROVED_ORDER','REJECTED_GRENKE'"
        ")",
        schema=SCHEMA,
    )
    op.add_column(
        "form_workflow_case",
        sa.Column("signature_deadline_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "form_workflow_case",
        sa.Column("resources_release_due_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "form_workflow_case",
        sa.Column("resources_released_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "form_workflow_case",
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "form_workflow_case",
        sa.Column("status_source", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "form_workflow_case",
        sa.Column("status_history", sa.JSON(), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_form_workflow_case_resources_release_due_at",
        "form_workflow_case",
        ["resources_release_due_at"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Cofa rozszerzenia archiwizacji i nowych statusów GenForm."""
    op.drop_index(
        "ix_form_workflow_case_resources_release_due_at",
        table_name="form_workflow_case",
        schema=SCHEMA,
    )
    op.drop_column("form_workflow_case", "status_history", schema=SCHEMA)
    op.drop_column("form_workflow_case", "status_source", schema=SCHEMA)
    op.drop_column("form_workflow_case", "status_changed_at", schema=SCHEMA)
    op.drop_column("form_workflow_case", "resources_released_at", schema=SCHEMA)
    op.drop_column("form_workflow_case", "resources_release_due_at", schema=SCHEMA)
    op.drop_column("form_workflow_case", "signature_deadline_at", schema=SCHEMA)
    op.drop_constraint(
        "form_workflow_case_business_status_check",
        "form_workflow_case",
        type_="check",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "form_workflow_case_business_status_check",
        "form_workflow_case",
        "business_status in ('DRAFT','PENDING_APPROVAL','APPROVED','ZEROWKA','REJECTED')",
        schema=SCHEMA,
    )

    op.drop_index("ix_form_request_archive_due_at", table_name="form_request", schema=SCHEMA)
    op.drop_index("ix_form_request_archive_bucket", table_name="form_request", schema=SCHEMA)
    op.drop_constraint(
        "form_request_archive_bucket_check",
        "form_request",
        type_="check",
        schema=SCHEMA,
    )
    op.drop_column("form_request", "archive_due_at", schema=SCHEMA)
    op.drop_column("form_request", "archived_at", schema=SCHEMA)
    op.drop_column("form_request", "archive_bucket", schema=SCHEMA)
