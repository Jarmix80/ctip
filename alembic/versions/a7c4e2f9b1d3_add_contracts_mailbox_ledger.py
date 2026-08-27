"""Dodanie rejestru wiadomości GRENKE i zamkniętych spraw historycznych.

Revision ID: a7c4e2f9b1d3
Revises: f9a0b1c2d3e4
Create Date: 2026-08-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a7c4e2f9b1d3"
down_revision: str | Sequence[str] | None = "f9a0b1c2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "ctip"


def _utc_now() -> sa.TextClause:
    return sa.text("timezone('utc', now())")


def upgrade() -> None:
    """Tworzy addytywny i idempotentny rejestr mailboxa."""
    op.create_table(
        "contracts_mailbox_history_case",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("application_no_raw", sa.Text(), nullable=False),
        sa.Column("application_no_normalized", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="historical_closed"),
        sa.Column("source", sa.Text(), nullable=False, server_default="mailbox_backfill"),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("case_metadata", sa.JSON(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=_utc_now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_utc_now()
        ),
        sa.Column(
            "archived_at", sa.DateTime(timezone=True), nullable=False, server_default=_utc_now()
        ),
        sa.CheckConstraint(
            "status = 'historical_closed'",
            name="contracts_mailbox_history_case_status_check",
        ),
        sa.UniqueConstraint(
            "application_no_normalized",
            name="uq_contracts_mailbox_history_case_application",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_contracts_mailbox_history_case_archived",
        "contracts_mailbox_history_case",
        ["archived_at", "id"],
        schema=SCHEMA,
    )

    op.create_table(
        "contracts_mailbox_message",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("message_id", sa.Text(), nullable=False),
        sa.Column("mailbox_folder", sa.Text(), nullable=False, server_default="INBOX"),
        sa.Column("imap_id", sa.Text(), nullable=True),
        sa.Column("processing_status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("classification", sa.Text(), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=True),
        sa.Column("application_no_raw", sa.Text(), nullable=True),
        sa.Column("application_no_normalized", sa.Text(), nullable=True),
        sa.Column("proforma_no_raw", sa.Text(), nullable=True),
        sa.Column("proforma_no_normalized", sa.Text(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=False, server_default=""),
        sa.Column("sender", sa.Text(), nullable=False, server_default=""),
        sa.Column("body_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("form_request_id", sa.Integer(), nullable=True),
        sa.Column("history_case_id", sa.Integer(), nullable=True),
        sa.Column(
            "attachment_manifest",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=_utc_now()
        ),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=_utc_now()
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=_utc_now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_utc_now()
        ),
        sa.CheckConstraint(
            "processing_status in ("
            "'pending','linked_form','historical_archived','ignored','manual_hold','error'"
            ")",
            name="contracts_mailbox_message_status_check",
        ),
        sa.CheckConstraint(
            "form_request_id is null or history_case_id is null",
            name="contracts_mailbox_message_single_target_check",
        ),
        sa.ForeignKeyConstraint(
            ["form_request_id"],
            ["ctip.form_request.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["history_case_id"],
            ["ctip.contracts_mailbox_history_case.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("message_id", name="uq_contracts_mailbox_message_message_id"),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_contracts_mailbox_message_status_received",
        "contracts_mailbox_message",
        ["processing_status", "received_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_contracts_mailbox_message_application",
        "contracts_mailbox_message",
        ["application_no_normalized"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_contracts_mailbox_message_form",
        "contracts_mailbox_message",
        ["form_request_id", "received_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_contracts_mailbox_message_history_case",
        "contracts_mailbox_message",
        ["history_case_id", "received_at"],
        schema=SCHEMA,
    )
    op.execute(
        "GRANT SELECT,INSERT,DELETE,UPDATE ON ctip.contracts_mailbox_history_case TO appuser"
    )
    op.execute("GRANT SELECT,INSERT,DELETE,UPDATE ON ctip.contracts_mailbox_message TO appuser")
    op.execute("GRANT ALL ON SEQUENCE ctip.contracts_mailbox_history_case_id_seq TO appuser")
    op.execute("GRANT ALL ON SEQUENCE ctip.contracts_mailbox_message_id_seq TO appuser")


def downgrade() -> None:
    """Usuwa wyłącznie addytywne tabele rejestru mailboxa."""
    op.drop_table("contracts_mailbox_message", schema=SCHEMA)
    op.drop_table("contracts_mailbox_history_case", schema=SCHEMA)
