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
        CheckConstraint(
            "role in ('admin','operator','serwisant')",
            name="admin_user_role_check",
        ),
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
            "archive_bucket is null or archive_bucket in ('accepted','rejected','unfilled','ksero_partner')",
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
            "'WAITING_SIGNATURE','APPROVED_ORDER','REJECTED_GRENKE','RENTAL_WITHOUT_GRENKE'"
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


class DeliveryCase(Base):
    """Sprawa obsługi dostawy, tworzona z FLOW GRENKE albo ręcznie."""

    __tablename__ = "delivery_case"
    __table_args__ = (
        CheckConstraint("source in ('grenke','manual')", name="delivery_case_source_check"),
        CheckConstraint("case_type in ('delivery','pickup')", name="delivery_case_type_check"),
        CheckConstraint(
            "status in ('new','planned','in_progress','done','cancelled')",
            name="delivery_case_status_check",
        ),
        UniqueConstraint("workflow_case_id", name="uq_delivery_case_workflow_case_id"),
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
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.admin_user.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[str] = mapped_column(Text, nullable=False, default="manual")
    case_type: Mapped[str] = mapped_column(Text, nullable=False, default="delivery")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="new")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    form_request_id: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.form_request.id", ondelete="SET NULL"), nullable=True
    )
    workflow_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.form_workflow_case.id", ondelete="SET NULL"), nullable=True
    )
    firebird_client_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    customer_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    customer_nip: Mapped[str | None] = mapped_column(Text, nullable=True)
    customer_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    customer_phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    delivery_time_window: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_contact_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_contact_phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    service_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    form_request: Mapped[FormRequest | None] = relationship()
    workflow_case: Mapped[FormWorkflowCase | None] = relationship()
    created_by_user: Mapped[AdminUser | None] = relationship(foreign_keys=[created_by])
    updated_by_user: Mapped[AdminUser | None] = relationship(foreign_keys=[updated_by])
    devices: Mapped[list[DeliveryCaseDevice]] = relationship(
        back_populates="delivery_case", cascade="all, delete-orphan"
    )
    tasks: Mapped[list[DeliveryCaseTask]] = relationship(
        back_populates="delivery_case", cascade="all, delete-orphan"
    )
    files: Mapped[list[DeliveryCaseFile]] = relationship(
        back_populates="delivery_case", cascade="all, delete-orphan"
    )


class DeliveryCaseDevice(Base):
    """Urządzenie przypisane do sprawy obsługi dostawy."""

    __tablename__ = "delivery_case_device"
    __table_args__ = (
        UniqueConstraint(
            "delivery_case_id",
            "workflow_device_id",
            name="uq_delivery_case_device_workflow_device_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    delivery_case_id: Mapped[int] = mapped_column(
        ForeignKey("ctip.delivery_case.id", ondelete="CASCADE"), nullable=False
    )
    workflow_device_id: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.form_workflow_device.id", ondelete="SET NULL"), nullable=True
    )
    producer: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    serial: Mapped[str | None] = mapped_column(Text, nullable=True)
    ewidencja: Mapped[str | None] = mapped_column(Text, nullable=True)
    firebird_machine_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    device_role: Mapped[str] = mapped_column(Text, nullable=False, default="delivery")
    source_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    delivery_case: Mapped[DeliveryCase] = relationship(back_populates="devices")
    workflow_device: Mapped[FormWorkflowDevice | None] = relationship()


class DeliveryCaseTask(Base):
    """Zadanie operacyjne w sprawie dostawy albo odbioru."""

    __tablename__ = "delivery_case_task"
    __table_args__ = (
        CheckConstraint(
            "task_type in ('delivery','preparation','pickup','zerowka','customer_contact','service_order','document','other')",
            name="delivery_case_task_type_check",
        ),
        CheckConstraint(
            "status in ('todo','planned','done','cancelled')",
            name="delivery_case_task_status_check",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    delivery_case_id: Mapped[int] = mapped_column(
        ForeignKey("ctip.delivery_case.id", ondelete="CASCADE"), nullable=False
    )
    task_type: Mapped[str] = mapped_column(Text, nullable=False, default="other")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="todo")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_time_window: Mapped[str | None] = mapped_column(Text, nullable=True)
    assignee_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.admin_user.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    delivery_case: Mapped[DeliveryCase] = relationship(back_populates="tasks")
    assignee_user: Mapped[AdminUser | None] = relationship()


class DeliveryCaseFile(Base):
    """Plik powiązany ze sprawą obsługi dostaw."""

    __tablename__ = "delivery_case_file"
    __table_args__ = (
        CheckConstraint(
            "source in ('upload','mailbox','generated')",
            name="delivery_case_file_source_check",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    delivery_case_id: Mapped[int] = mapped_column(
        ForeignKey("ctip.delivery_case.id", ondelete="CASCADE"), nullable=False
    )
    file_type: Mapped[str] = mapped_column(Text, nullable=False, default="other")
    source: Mapped[str] = mapped_column(Text, nullable=False, default="upload")
    file_name: Mapped[str] = mapped_column(Text, nullable=False)
    original_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uploaded_by: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.admin_user.id", ondelete="SET NULL"), nullable=True
    )
    snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    delivery_case: Mapped[DeliveryCase] = relationship(back_populates="files")
    uploaded_by_user: Mapped[AdminUser | None] = relationship()


class DeliveryDocumentTemplate(Base):
    """Rejestr szablonów dokumentów używanych w obsłudze dostaw."""

    __tablename__ = "delivery_document_template"
    __table_args__ = (UniqueConstraint("template_key", name="uq_delivery_document_template_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    template_key: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    document_type: Mapped[str] = mapped_column(Text, nullable=False, default="other")
    template_path: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    required_fields: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class GrenkeContractEnd(Base):
    """Wpis kalendarza końca umowy GRENKE wymagający potwierdzenia operatora."""

    __tablename__ = "grenke_contract_end"
    __table_args__ = (
        CheckConstraint(
            "status in ('pending_confirmation','confirmed','cancelled')",
            name="grenke_contract_end_status_check",
        ),
        UniqueConstraint("workflow_case_id", name="uq_grenke_contract_end_workflow_case_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    delivery_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.delivery_case.id", ondelete="CASCADE"), nullable=True
    )
    form_request_id: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.form_request.id", ondelete="SET NULL"), nullable=True
    )
    workflow_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.form_workflow_case.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending_confirmation")
    prefilled_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    confirmed_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.admin_user.id", ondelete="SET NULL"), nullable=True
    )
    customer_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    contract_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    notification_history: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    delivery_case: Mapped[DeliveryCase | None] = relationship()
    form_request: Mapped[FormRequest | None] = relationship()
    workflow_case: Mapped[FormWorkflowCase | None] = relationship()
    confirmed_by_user: Mapped[AdminUser | None] = relationship(foreign_keys=[confirmed_by])


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
Index("idx_delivery_case_source_status", DeliveryCase.source, DeliveryCase.status)
Index("idx_delivery_case_type_status", DeliveryCase.case_type, DeliveryCase.status)
Index("idx_delivery_case_delivery_date", DeliveryCase.delivery_date)
Index("idx_delivery_case_firebird_client", DeliveryCase.firebird_client_id)
Index("idx_delivery_case_device_case", DeliveryCaseDevice.delivery_case_id)
Index("idx_delivery_case_device_machine", DeliveryCaseDevice.firebird_machine_id)
Index("idx_delivery_case_task_case", DeliveryCaseTask.delivery_case_id)
Index("idx_delivery_case_task_due", DeliveryCaseTask.status, DeliveryCaseTask.due_date)
Index("idx_delivery_case_file_case", DeliveryCaseFile.delivery_case_id)
Index("idx_delivery_document_template_active", DeliveryDocumentTemplate.active)
Index(
    "idx_grenke_contract_end_status_date",
    GrenkeContractEnd.status,
    GrenkeContractEnd.confirmed_end_date,
)
Index(
    "idx_grenke_contract_end_pending_prefill",
    GrenkeContractEnd.status,
    GrenkeContractEnd.prefilled_end_date,
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
    "DeliveryCase",
    "DeliveryCaseDevice",
    "DeliveryCaseFile",
    "DeliveryCaseTask",
    "DeliveryDocumentTemplate",
    "GrenkeContractEnd",
]
