"""Modele ORM dla modułu administratora."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class AdminUser(Base):
    """Użytkownik panelu administracyjnego."""

    __tablename__ = "admin_user"
    __table_args__ = (
        CheckConstraint("role in ('admin','operator')", name="admin_user_role_check"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    first_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    internal_ext: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(Text, nullable=False, default="admin")
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_salesperson: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    mobile_phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    firebird_app_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    firebird_app_user_login: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )

    sessions: Mapped[list[AdminSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    audit_entries: Mapped[list[AdminAuditLog]] = relationship(
        back_populates="user", cascade="save-update"
    )


class AdminSession(Base):
    """Sesja uwierzytelniająca administratora."""

    __tablename__ = "admin_session"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("ctip.admin_user.id", ondelete="CASCADE"))
    token: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    client_ip: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[AdminUser] = relationship(back_populates="sessions")


class AdminSetting(Base):
    """Pojedyncze ustawienie konfiguracyjne panelu."""

    __tablename__ = "admin_setting"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    is_secret: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.admin_user.id", ondelete="SET NULL"), nullable=True
    )

    updated_by_user: Mapped[AdminUser | None] = relationship()


class AdminAuditLog(Base):
    """Dziennik operacji administratora."""

    __tablename__ = "admin_audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.admin_user.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    client_ip: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[AdminUser | None] = relationship(back_populates="audit_entries")


class FormRequest(Base):
    """Wniosek o wygenerowanie jednorazowego formularza dla klienta."""

    __tablename__ = "form_request"
    __table_args__ = (
        CheckConstraint(
            "status in ('GENERATED','DISPATCHED','SUBMITTED','EXPIRED')",
            name="form_request_status_check",
        ),
        CheckConstraint(
            "archive_bucket is null or archive_bucket in ('accepted','rejected','unfilled')",
            name="form_request_archive_bucket_check",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.admin_user.id", ondelete="SET NULL"), nullable=True
    )
    customer_name: Mapped[str] = mapped_column(Text, nullable=False)
    customer_email: Mapped[str] = mapped_column(Text, nullable=False)
    customer_phone: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="GENERATED")
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    token_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    token_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sms_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    email_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    ms_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    notification_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archive_bucket: Mapped[str | None] = mapped_column(Text, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archive_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_by_user: Mapped[AdminUser | None] = relationship()


class FormWorkflowCase(Base):
    """Sprawa workflow powiązana z pojedynczym formularzem klienta."""

    __tablename__ = "form_workflow_case"
    __table_args__ = (
        CheckConstraint(
            "stage in ('FORM_SUBMITTED','CLIENT_READY','DEVICES_SELECTED','PROFORMA_CREATED')",
            name="form_workflow_case_stage_check",
        ),
        CheckConstraint(
            "business_status in ("
            "'DRAFT','PENDING_APPROVAL','APPROVED','ZEROWKA','REJECTED',"
            "'WAITING_SIGNATURE','APPROVED_ORDER','REJECTED_GRENKE'"
            ")",
            name="form_workflow_case_business_status_check",
        ),
        UniqueConstraint("form_request_id", name="uq_form_workflow_case_form_request_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    form_request_id: Mapped[int] = mapped_column(
        ForeignKey("ctip.form_request.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.admin_user.id", ondelete="SET NULL"), nullable=True
    )
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.admin_user.id", ondelete="SET NULL"), nullable=True
    )
    stage: Mapped[str] = mapped_column(Text, nullable=False, default="FORM_SUBMITTED")
    business_status: Mapped[str] = mapped_column(Text, nullable=False, default="DRAFT")
    client_mode: Mapped[str | None] = mapped_column(Text, nullable=True)
    firebird_client_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    firebird_client_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_payload_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    proforma_firebird_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    proforma_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    proforma_pdf_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    signature_deadline_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resources_release_due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resources_released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_history: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    delivery_time_window: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_contact_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_contact_phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    form_request: Mapped[FormRequest] = relationship()
    created_by_user: Mapped[AdminUser | None] = relationship(
        foreign_keys=[created_by], overlaps="created_by_user"
    )
    updated_by_user: Mapped[AdminUser | None] = relationship(
        foreign_keys=[updated_by], overlaps="created_by_user"
    )
    devices: Mapped[list[FormWorkflowDevice]] = relationship(
        back_populates="workflow_case", cascade="all, delete-orphan"
    )


class FormWorkflowDevice(Base):
    """Urządzenie robocze wybrane do sprawy formularza po stronie CTIP."""

    __tablename__ = "form_workflow_device"
    __table_args__ = (
        CheckConstraint(
            "source_type in ('google_sheet','firebird_magazyn_28','firebird_serial')",
            name="form_workflow_device_source_type_check",
        ),
        UniqueConstraint(
            "workflow_case_id",
            "source_type",
            "source_row",
            name="uq_form_workflow_device_source_row",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    workflow_case_id: Mapped[int] = mapped_column(
        ForeignKey("ctip.form_workflow_case.id", ondelete="CASCADE"), nullable=False
    )
    source_type: Mapped[str] = mapped_column(Text, nullable=False, default="google_sheet")
    source_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    producer: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    serial: Mapped[str | None] = mapped_column(Text, nullable=True)
    ewidencja: Mapped[str | None] = mapped_column(Text, nullable=True)
    device_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    reservation_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    price: Mapped[str | None] = mapped_column(Text, nullable=True)
    price_net: Mapped[str | None] = mapped_column(Text, nullable=True)
    price_gross: Mapped[str | None] = mapped_column(Text, nullable=True)
    firebird_machine_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    firebird_client_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    workflow_case: Mapped[FormWorkflowCase] = relationship(back_populates="devices")


class WorkflowSheetStatusCache(Base):
    """Lokalny cache statusów urządzeń z arkusza Google dla modalu FLOW."""

    __tablename__ = "workflow_sheet_status_cache"
    __table_args__ = (
        CheckConstraint(
            "source_type in ('google_sheet','firebird_magazyn_28','firebird_serial')",
            name="workflow_sheet_status_cache_source_type_check",
        ),
        UniqueConstraint("source_key", name="uq_workflow_sheet_status_cache_source_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(Text, nullable=False, default="firebird_magazyn_28")
    source_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    device_index: Mapped[str | None] = mapped_column(Text, nullable=True)
    device_index_normalized: Mapped[str | None] = mapped_column(Text, nullable=True)
    sheet_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sheet_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    reservation_grenke: Mapped[str | None] = mapped_column(Text, nullable=True)
    form_ctip: Mapped[str | None] = mapped_column(Text, nullable=True)
    ctip_form_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ctip_workflow_case_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    business_status_legacy: Mapped[str | None] = mapped_column(Text, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


Index("idx_form_request_status_created", FormRequest.status, FormRequest.created_at.desc())
Index("idx_form_request_created_by", FormRequest.created_by, FormRequest.created_at.desc())
Index(
    "idx_form_workflow_case_form_request",
    FormWorkflowCase.form_request_id,
)
Index(
    "idx_form_workflow_device_case",
    FormWorkflowDevice.workflow_case_id,
)
Index(
    "idx_workflow_sheet_status_cache_index_norm",
    WorkflowSheetStatusCache.device_index_normalized,
)


__all__ = [
    "AdminUser",
    "AdminSession",
    "AdminSetting",
    "AdminAuditLog",
    "FormRequest",
    "FormWorkflowCase",
    "FormWorkflowDevice",
    "WorkflowSheetStatusCache",
]
