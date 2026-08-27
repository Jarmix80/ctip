"""Trwały rejestr wiadomości mailboxa GRENKE i spraw historycznych."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class ContractsMailboxHistoryCase(Base):
    """Zamknięta sprawa historyczna utworzona bez formularza klienta."""

    __tablename__ = "contracts_mailbox_history_case"
    __table_args__ = (
        CheckConstraint(
            "status = 'historical_closed'",
            name="contracts_mailbox_history_case_status_check",
        ),
        Index(
            "idx_contracts_mailbox_history_case_archived",
            "archived_at",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_no_raw: Mapped[str] = mapped_column(Text, nullable=False)
    application_no_normalized: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="historical_closed",
        server_default="historical_closed",
    )
    source: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="mailbox_backfill",
        server_default="mailbox_backfill",
    )
    message_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    first_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    case_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.timezone("utc", func.now()),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.timezone("utc", func.now()),
    )
    archived_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.timezone("utc", func.now()),
    )


class ContractsMailboxMessage(Base):
    """Idempotentny zapis klasyfikacji pojedynczej wiadomości IMAP."""

    __tablename__ = "contracts_mailbox_message"
    __table_args__ = (
        CheckConstraint(
            "processing_status in ("
            "'pending','linked_form','historical_archived','ignored','manual_hold','error'"
            ")",
            name="contracts_mailbox_message_status_check",
        ),
        CheckConstraint(
            "form_request_id is null or history_case_id is null",
            name="contracts_mailbox_message_single_target_check",
        ),
        Index(
            "idx_contracts_mailbox_message_status_received",
            "processing_status",
            "received_at",
        ),
        Index(
            "idx_contracts_mailbox_message_application",
            "application_no_normalized",
        ),
        Index(
            "idx_contracts_mailbox_message_form",
            "form_request_id",
            "received_at",
        ),
        Index(
            "idx_contracts_mailbox_message_history_case",
            "history_case_id",
            "received_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    mailbox_folder: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="INBOX",
        server_default="INBOX",
    )
    imap_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    processing_status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="pending",
        server_default="pending",
    )
    classification: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    application_no_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    application_no_normalized: Mapped[str | None] = mapped_column(Text, nullable=True)
    proforma_no_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    proforma_no_normalized: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    sender: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    body_text: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    form_request_id: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.form_request.id", ondelete="SET NULL"),
        nullable=True,
    )
    history_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.contracts_mailbox_history_case.id", ondelete="CASCADE"),
        nullable=True,
    )
    attachment_manifest: Mapped[list[dict]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
        server_default="[]",
    )
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.timezone("utc", func.now()),
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.timezone("utc", func.now()),
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.timezone("utc", func.now()),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.timezone("utc", func.now()),
    )
