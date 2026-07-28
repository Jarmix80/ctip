"""Modele trwałego Centrum Obsługi i izolowanego laboratorium zgłoszeń."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _uuid() -> str:
    return str(uuid4())


class CrmCase(Base):
    """Sprawa przyjęta z kanału klienta albo utworzona ręcznie."""

    __tablename__ = "crm_case"
    __table_args__ = (
        CheckConstraint(
            "queue in ('sales','service_it','accounting','contracts','meters','other')",
            name="ck_crm_case_queue",
        ),
        CheckConstraint(
            "status in ('new','active','transferred','done','archived')",
            name="ck_crm_case_status",
        ),
        CheckConstraint(
            "priority in ('low','normal','high')",
            name="ck_crm_case_priority",
        ),
        CheckConstraint(
            "source_channel in "
            "('chat','voice','form','configurator','email','phone','manual','scenario')",
            name="ck_crm_case_source_channel",
        ),
        UniqueConstraint(
            "source_channel",
            "external_ref",
            name="uq_crm_case_source_external_ref",
        ),
        Index("idx_crm_case_queue_status", "queue", "status"),
        Index("idx_crm_case_is_lab_retained_until", "is_lab", "retained_until"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ref: Mapped[str] = mapped_column(Text, unique=True, nullable=False, default=_uuid)
    external_ref: Mapped[str] = mapped_column(Text, nullable=False)
    conversation_ref: Mapped[str | None] = mapped_column(Text)
    source_channel: Mapped[str] = mapped_column(Text, nullable=False)
    source_detail: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    queue: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="new")
    priority: Mapped[str] = mapped_column(Text, nullable=False, default="normal")
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    company_name: Mapped[str] = mapped_column(Text, nullable=False)
    contact_name: Mapped[str] = mapped_column(Text, nullable=False)
    contact_phone: Mapped[str | None] = mapped_column(Text)
    contact_email: Mapped[str | None] = mapped_column(Text)
    customer_ref: Mapped[str | None] = mapped_column(Text)
    identity_status: Mapped[str | None] = mapped_column(Text)
    device_label: Mapped[str | None] = mapped_column(Text)
    device_refs: Mapped[list[str] | None] = mapped_column(JSON)
    device_serial_last4: Mapped[str | None] = mapped_column(Text)
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.admin_user.id", ondelete="SET NULL")
    )
    owner_name: Mapped[str | None] = mapped_column(Text)
    declared_operator_id: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.admin_user.id", ondelete="SET NULL")
    )
    ms_order_ref: Mapped[str | None] = mapped_column(Text)
    idempotency_key_hash: Mapped[str | None] = mapped_column(Text, unique=True)
    source_payload: Mapped[dict | None] = mapped_column(JSON)
    is_lab: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now
    )
    first_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retained_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    events: Mapped[list[CrmCaseEvent]] = relationship(
        back_populates="case",
        cascade="all, delete-orphan",
        order_by="CrmCaseEvent.created_at",
    )


class CrmCaseEvent(Base):
    """Niemodyfikowalne zdarzenie osi czasu sprawy."""

    __tablename__ = "crm_case_event"
    __table_args__ = (Index("idx_crm_case_event_case_created", "case_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ref: Mapped[str] = mapped_column(Text, unique=True, nullable=False, default=_uuid)
    case_id: Mapped[int] = mapped_column(
        ForeignKey("ctip.crm_case.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.admin_user.id", ondelete="SET NULL")
    )
    actor_name: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    case: Mapped[CrmCase] = relationship(back_populates="events")
