"""Modele ORM dla modułu administratora."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class AdminUser(Base):
    """Użytkownik panelu administracyjnego."""

    __tablename__ = "admin_user"
    __table_args__ = (
        CheckConstraint("role in ('admin','operator')", name="admin_user_role_check"),
        CheckConstraint(
            "device_theme in ('blue','graphite','mint')",
            name="admin_user_device_theme_check",
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
    can_withdraw_device_pz: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    device_theme: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="blue",
        server_default="blue",
    )
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
            "archive_bucket is null or archive_bucket in "
            "('accepted','rejected','unfilled','ksero_partner','closed_other')",
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
            "'WAITING_SIGNATURE','APPROVED_ORDER','REJECTED_GRENKE',"
            "'RENTAL_WITHOUT_GRENKE','CLOSED_NOT_REALIZED'"
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
    producer: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    serial: Mapped[str | None] = mapped_column(Text, nullable=True)
    device_index: Mapped[str | None] = mapped_column(Text, nullable=True)
    device_index_normalized: Mapped[str | None] = mapped_column(Text, nullable=True)
    sheet_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sheet_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    sheet_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    counter_bw: Mapped[str | None] = mapped_column(Text, nullable=True)
    counter_color: Mapped[str | None] = mapped_column(Text, nullable=True)
    counter_scan: Mapped[str | None] = mapped_column(Text, nullable=True)
    reservation_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    reservation_grenke: Mapped[str | None] = mapped_column(Text, nullable=True)
    reservation_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    price: Mapped[str | None] = mapped_column(Text, nullable=True)
    ms_id_maszyna: Mapped[int | None] = mapped_column(Integer, nullable=True)
    form_ctip: Mapped[str | None] = mapped_column(Text, nullable=True)
    ctip_form_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ctip_workflow_case_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    business_status_legacy: Mapped[str | None] = mapped_column(Text, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DeviceIntakeOperation(Base):
    """Idempotentna operacja przyjęcia urządzeń do magazynu Firebird."""

    __tablename__ = "device_intake_operation"
    __table_args__ = (
        CheckConstraint(
            "status in ('processing','completed','failed','reconcile_required','withdrawn')",
            name="device_intake_operation_status_check",
        ),
        UniqueConstraint("idempotency_key", name="uq_device_intake_operation_idempotency_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="processing")
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.admin_user.id", ondelete="SET NULL"), nullable=True
    )
    supplier_firebird_id: Mapped[int] = mapped_column(Integer, nullable=False)
    external_document: Mapped[str | None] = mapped_column(Text, nullable=True)
    exception_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    firebird_pz_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    firebird_pz_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    result_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    withdrawn_by: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.admin_user.id", ondelete="SET NULL"), nullable=True
    )
    withdrawal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    withdrawal_preview: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DeviceInventoryUnit(Base):
    """Trwałe mapowanie fizycznego egzemplarza między CTIP i Firebird."""

    __tablename__ = "device_inventory_unit"
    __table_args__ = (
        CheckConstraint(
            "source_type = 'firebird_magazyn_28'",
            name="device_inventory_unit_source_type_check",
        ),
        CheckConstraint(
            "status in ('active','withdrawn')",
            name="device_inventory_unit_status_check",
        ),
        UniqueConstraint(
            "source_type",
            "source_row",
            name="uq_device_inventory_unit_source",
        ),
        UniqueConstraint(
            "firebird_zakpozycja_id",
            name="uq_device_inventory_unit_zakpozycja",
        ),
        UniqueConstraint(
            "firebird_machine_table_id",
            name="uq_device_inventory_unit_machine_table",
        ),
        Index(
            "uq_device_inventory_unit_serial_normalized",
            "serial_normalized",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
        Index(
            "uq_device_inventory_unit_ewidencja_normalized",
            "ewidencja_normalized",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    operation_id: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.device_intake_operation.id", ondelete="SET NULL"), nullable=True
    )
    source_type: Mapped[str] = mapped_column(Text, nullable=False, default="firebird_magazyn_28")
    source_row: Mapped[int] = mapped_column(Integer, nullable=False)
    firebird_pz_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    firebird_zakpozycja_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    firebird_machine_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    firebird_machine_table_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    firebird_model_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    firebird_supplier_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    serial: Mapped[str] = mapped_column(Text, nullable=False)
    serial_normalized: Mapped[str] = mapped_column(Text, nullable=False)
    ewidencja: Mapped[str] = mapped_column(Text, nullable=False)
    ewidencja_normalized: Mapped[str] = mapped_column(Text, nullable=False)
    purchase_price_net: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    sheet_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sheet_sync_status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    sheet_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )


class DeviceInventoryEvent(Base):
    """Niemodyfikowalny dziennik uwag, rezerwacji i synchronizacji urządzenia."""

    __tablename__ = "device_inventory_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    unit_id: Mapped[int] = mapped_column(
        ForeignKey("ctip.device_inventory_unit.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.admin_user.id", ondelete="SET NULL"), nullable=True
    )
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )


class DeviceCounterReading(Base):
    """Historyczny odczyt liczników fizycznego egzemplarza urządzenia."""

    __tablename__ = "device_counter_reading"
    __table_args__ = (
        CheckConstraint(
            "source in ('intake','manual')",
            name="device_counter_reading_source_check",
        ),
        CheckConstraint(
            "counter_bw is not null or counter_color is not null or counter_scan is not null",
            name="device_counter_reading_value_check",
        ),
        CheckConstraint(
            "(counter_bw is null or counter_bw >= 0) and "
            "(counter_color is null or counter_color >= 0) and "
            "(counter_scan is null or counter_scan >= 0)",
            name="device_counter_reading_nonnegative_check",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    unit_id: Mapped[int] = mapped_column(
        ForeignKey("ctip.device_inventory_unit.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    reading_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    counter_bw: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    counter_color: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    counter_scan: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    applied_to_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.admin_user.id", ondelete="SET NULL"), nullable=True
    )
    source_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )


class DeviceManualReservation(Base):
    """Ręczna, terminowa rezerwacja egzemplarza poza aktywnym FLOW."""

    __tablename__ = "device_manual_reservation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    unit_id: Mapped[int] = mapped_column(
        ForeignKey("ctip.device_inventory_unit.id", ondelete="CASCADE"), nullable=False
    )
    reserved_for: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.admin_user.id", ondelete="SET NULL"), nullable=True
    )
    released_by: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.admin_user.id", ondelete="SET NULL"), nullable=True
    )
    release_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DeviceSheetOutbox(Base):
    """Kolejka niezawodnej synchronizacji urządzeń do Google Sheets."""

    __tablename__ = "device_sheet_outbox"
    __table_args__ = (
        CheckConstraint(
            "operation_type in ("
            "'upsert_device','update_note','update_counters','delete_device',"
            "'update_reservation','release_reservation'"
            ")",
            name="device_sheet_outbox_operation_type_check",
        ),
        CheckConstraint(
            "status in ('pending','processing','completed','failed')",
            name="device_sheet_outbox_status_check",
        ),
        UniqueConstraint("idempotency_key", name="uq_device_sheet_outbox_idempotency_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    unit_id: Mapped[int] = mapped_column(
        ForeignKey("ctip.device_inventory_unit.id", ondelete="CASCADE"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    operation_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DeviceAuditRun(Base):
    """Trwały przebieg ręcznego audytu spójności urządzeń."""

    __tablename__ = "device_audit_run"
    __table_args__ = (
        CheckConstraint(
            "status in ('pending','running','completed','failed')",
            name="device_audit_run_status_check",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    requested_by: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.admin_user.id", ondelete="SET NULL"), nullable=True
    )
    phase: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_items: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_items: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DeviceAuditItem(Base):
    """Wynik audytu jednego urządzenia lub niejednoznacznej grupy rekordów."""

    __tablename__ = "device_audit_item"
    __table_args__ = (
        CheckConstraint(
            "result_status in ('ok','missing','discrepancy','duplicate')",
            name="device_audit_item_result_status_check",
        ),
        UniqueConstraint(
            "run_id",
            "canonical_key",
            name="uq_device_audit_item_run_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("ctip.device_audit_run.id", ondelete="CASCADE"), nullable=False
    )
    canonical_key: Mapped[str] = mapped_column(Text, nullable=False)
    producer: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    serial: Mapped[str | None] = mapped_column(Text, nullable=True)
    ewidencja: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sheet_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    machine_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ctip_unit_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sheet_present: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    warehouse_present: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    machine_present: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ctip_present: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    result_status: Mapped[str] = mapped_column(Text, nullable=False)
    issue_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    issue_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )


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
Index(
    "uq_device_manual_reservation_active",
    DeviceManualReservation.unit_id,
    unique=True,
    postgresql_where=DeviceManualReservation.released_at.is_(None),
    sqlite_where=DeviceManualReservation.released_at.is_(None),
)
Index(
    "idx_device_sheet_outbox_pending",
    DeviceSheetOutbox.status,
    DeviceSheetOutbox.next_attempt_at,
)
Index(
    "idx_device_inventory_event_unit_created",
    DeviceInventoryEvent.unit_id,
    DeviceInventoryEvent.created_at.desc(),
)
Index(
    "idx_device_counter_reading_unit_date",
    DeviceCounterReading.unit_id,
    DeviceCounterReading.reading_at.desc(),
)
Index(
    "idx_device_audit_run_status_created",
    DeviceAuditRun.status,
    DeviceAuditRun.created_at.desc(),
)
Index(
    "idx_device_audit_item_run_result",
    DeviceAuditItem.run_id,
    DeviceAuditItem.result_status,
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
    "DeviceIntakeOperation",
    "DeviceInventoryUnit",
    "DeviceInventoryEvent",
    "DeviceCounterReading",
    "DeviceManualReservation",
    "DeviceSheetOutbox",
    "DeviceAuditRun",
    "DeviceAuditItem",
]
