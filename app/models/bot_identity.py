"""Modele wspólnego katalogu tożsamości klientów obsługiwanych przez boty."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _uuid() -> str:
    return str(uuid4())


class BotIdentityCustomer(Base):
    """Minimalna projekcja klienta Menadżera Serwisu."""

    __tablename__ = "bot_identity_customer"
    __table_args__ = (Index("idx_bot_identity_customer_nip_hmac", "nip_hmac"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_ref: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    company_name: Mapped[str] = mapped_column(Text, nullable=False)
    nip_enc: Mapped[str | None] = mapped_column(Text)
    nip_hmac: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_seen_sync_id: Mapped[str | None] = mapped_column(Text)
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )


class BotIdentitySubject(Base):
    """Osoba albo techniczny podmiot powiązany z autoryzowanym numerem."""

    __tablename__ = "bot_identity_subject"
    __table_args__ = (
        UniqueConstraint("source", "external_ref", name="uq_bot_identity_subject_source_ref"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    external_ref: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source_revision: Mapped[str | None] = mapped_column(Text)
    last_seen_sync_id: Mapped[str | None] = mapped_column(Text)
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )


class BotIdentityPhone(Base):
    """Zaszyfrowany telefon przypisany do podmiotu katalogowego."""

    __tablename__ = "bot_identity_phone"
    __table_args__ = (
        UniqueConstraint("subject_id", "phone_hmac", name="uq_bot_identity_phone_subject"),
        Index("idx_bot_identity_phone_hmac", "phone_hmac"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("ctip.bot_identity_subject.id", ondelete="CASCADE"), nullable=False
    )
    phone_enc: Mapped[str] = mapped_column(Text, nullable=False)
    phone_hmac: Mapped[str] = mapped_column(Text, nullable=False)
    phone_last4: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_seen_sync_id: Mapped[str | None] = mapped_column(Text)
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )


class BotIdentityBinding(Base):
    """Powiązanie osoby z firmą wraz z pochodzeniem i poziomem zatwierdzenia."""

    __tablename__ = "bot_identity_binding"
    __table_args__ = (
        UniqueConstraint(
            "subject_id",
            "customer_id",
            "source",
            name="uq_bot_identity_binding_subject_customer_source",
        ),
        CheckConstraint(
            "trust_state in ('trusted','self_declared','operator_approved','disputed','inactive')",
            name="ck_bot_identity_binding_trust_state",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("ctip.bot_identity_subject.id", ondelete="CASCADE"), nullable=False
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("ctip.bot_identity_customer.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    trust_state: Mapped[str] = mapped_column(Text, nullable=False)
    source_case_ref: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_seen_sync_id: Mapped[str | None] = mapped_column(Text)
    last_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )


class BotIdentityDevice(Base):
    """Urządzenie klienta dostępne po deterministycznym potwierdzeniu powiązania."""

    __tablename__ = "bot_identity_device"
    __table_args__ = (
        UniqueConstraint("customer_id", "external_ref", name="uq_bot_identity_device_customer_ref"),
        UniqueConstraint("device_ref", name="uq_bot_identity_device_ref"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("ctip.bot_identity_customer.id", ondelete="CASCADE"), nullable=False
    )
    external_ref: Mapped[str] = mapped_column(Text, nullable=False)
    device_ref: Mapped[str] = mapped_column(Text, nullable=False, default=_uuid)
    producer: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    serial_enc: Mapped[str | None] = mapped_column(Text)
    serial_last4: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_seen_sync_id: Mapped[str | None] = mapped_column(Text)
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )


class BotIdentityOverride(Base):
    """Jawne rozstrzygnięcie operatora dla niejednoznacznego numeru."""

    __tablename__ = "bot_identity_override"

    id: Mapped[int] = mapped_column(primary_key=True)
    phone_hmac: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("ctip.bot_identity_subject.id", ondelete="CASCADE"), nullable=False
    )
    binding_id: Mapped[int] = mapped_column(
        ForeignKey("ctip.bot_identity_binding.id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    set_by_user_id: Mapped[int | None] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now
    )


class BotIdentitySyncRun(Base):
    """Audyt atomowego przebiegu synchronizacji Firebirda."""

    __tablename__ = "bot_identity_sync_run"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    source: Mapped[str] = mapped_column(Text, nullable=False, default="firebird_mobile_account")
    source_revision: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="running")
    accounts_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    customers_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    devices_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicate_phones: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BotIdentityResolution(Base):
    """Krótkotrwały wynik rozpoznania wymagający potwierdzenia w tej samej rozmowie."""

    __tablename__ = "bot_identity_resolution"
    __table_args__ = (
        CheckConstraint(
            "status in ('exact','ambiguous','not_found','disputed','stale')",
            name="ck_bot_identity_resolution_status",
        ),
    )

    ref: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    phone_hmac: Mapped[str] = mapped_column(Text, nullable=False)
    subject_id: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.bot_identity_subject.id", ondelete="SET NULL")
    )
    binding_id: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.bot_identity_binding.id", ondelete="SET NULL")
    )
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    conversation_ref: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    nip_failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    nip_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )


class BotDisclosureGrant(Base):
    """Haszowany grant zezwalający na kontrolowany odczyt urządzeń."""

    __tablename__ = "bot_disclosure_grant"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    token_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    resolution_ref: Mapped[str] = mapped_column(
        ForeignKey("ctip.bot_identity_resolution.ref", ondelete="CASCADE"), nullable=False
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("ctip.bot_identity_customer.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    conversation_ref: Mapped[str] = mapped_column(Text, nullable=False)
    disclosure_level: Mapped[str] = mapped_column(Text, nullable=False, default="full_serial")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )


class BotIdentitySmsChallenge(Base):
    """Krótkotrwałe, haszowane wyzwanie SMS używane wyłącznie przez kanał testowy."""

    __tablename__ = "bot_identity_sms_challenge"

    ref: Mapped[str] = mapped_column(Text, primary_key=True, default=_uuid)
    phone_hmac: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    conversation_ref: Mapped[str] = mapped_column(Text, nullable=False)
    code_hash: Mapped[str] = mapped_column(Text, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
