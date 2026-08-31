"""Modele ORM modułu wysyłek części i materiałów eksploatacyjnych."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class ShippingAddress(Base):
    """Zweryfikowany adres dostawy zapamiętany dla klienta lub lokalizacji urządzenia."""

    __tablename__ = "shipping_address"
    __table_args__ = (
        UniqueConstraint(
            "firebird_client_id",
            "location_key",
            name="uq_shipping_address_client_location",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    firebird_client_id: Mapped[int] = mapped_column(Integer, nullable=False)
    firebird_machine_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    location_key: Mapped[str] = mapped_column(Text, nullable=False)
    location_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    location_text_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    location_fingerprint: Mapped[str | None] = mapped_column(Text, nullable=True)
    company_name: Mapped[str] = mapped_column(Text, nullable=False)
    contact_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    street: Mapped[str] = mapped_column(Text, nullable=False)
    postal_code: Mapped[str] = mapped_column(Text, nullable=False)
    city: Mapped[str] = mapped_column(Text, nullable=False)
    country_code: Mapped[str] = mapped_column(Text, nullable=False, default="PL")
    phone: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    verified_by: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.admin_user.id", ondelete="SET NULL"), nullable=True
    )
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )


class ShippingConsumableCompatibility(Base):
    """Sugestia lub zatwierdzone mapowanie modelu na fizyczną kartotekę magazynową."""

    __tablename__ = "shipping_consumable_compatibility"
    __table_args__ = (
        CheckConstraint(
            "status in ('suggested','confirmed','rejected','stale')",
            name="shipping_compatibility_status_check",
        ),
        CheckConstraint(
            "confidence is null or confidence in ('high','medium','low')",
            name="shipping_compatibility_confidence_check",
        ),
        UniqueConstraint(
            "firebird_model_id",
            "firebird_warehouse_item_id",
            name="uq_shipping_compatibility_model_item",
        ),
        Index("idx_shipping_compatibility_model", "firebird_model_id"),
        Index("idx_shipping_compatibility_status", "status", "confidence"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    firebird_model_id: Mapped[int] = mapped_column(Integer, nullable=False)
    firebird_warehouse_item_id: Mapped[int] = mapped_column(Integer, nullable=False)
    model_label: Mapped[str] = mapped_column(Text, nullable=False)
    item_index: Mapped[str | None] = mapped_column(Text, nullable=True)
    item_name: Mapped[str] = mapped_column(Text, nullable=False)
    item_kind: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="suggested")
    confidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    source_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    reviewed_by: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.admin_user.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmed_by: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.admin_user.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )


class ShippingCase(Base):
    """Stan, adres, rozliczenie i rezerwacje zlecenia dowozu materiałów z Firebirda."""

    __tablename__ = "shipping_case"
    __table_args__ = (
        CheckConstraint(
            "status in ('review_pending','ready','shipment_created','handed_over','closed',"
            "'manual_billing','reconcile_required')",
            name="shipping_case_status_check",
        ),
        UniqueConstraint("firebird_order_table_id", name="uq_shipping_case_firebird_order"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    firebird_order_table_id: Mapped[int] = mapped_column(Integer, nullable=False)
    firebird_order_id: Mapped[int] = mapped_column(Integer, nullable=False)
    firebird_order_year: Mapped[int] = mapped_column(Integer, nullable=False)
    firebird_client_id: Mapped[int] = mapped_column(Integer, nullable=False)
    firebird_machine_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    firebird_model_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    order_kind: Mapped[str | None] = mapped_column(Text, nullable=True)
    invoice_required: Mapped[bool] = mapped_column(nullable=False, default=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="review_pending")
    address_id: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.shipping_address.id", ondelete="SET NULL"), nullable=True
    )
    address_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    source_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    location_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    location_text_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    location_fingerprint: Mapped[str | None] = mapped_column(Text, nullable=True)
    weight_kg: Mapped[Decimal] = mapped_column(Numeric(8, 3), nullable=False)
    label_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.admin_user.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )

    items: Mapped[list[ShippingItem]] = relationship(
        back_populates="shipping_case", cascade="all, delete-orphan"
    )
    shipment: Mapped[ShippingShipment | None] = relationship(
        back_populates="shipping_case", uselist=False
    )


class ShippingItem(Base):
    """Wybrana pozycja wraz z ceną zaakceptowaną dla dokumentów MS."""

    __tablename__ = "shipping_item"
    __table_args__ = (
        UniqueConstraint(
            "shipping_case_id",
            "firebird_warehouse_item_id",
            name="uq_shipping_item_case_warehouse",
        ),
        CheckConstraint(
            "price_source in ('sale','purchase_fallback','purchase_contract','manual')",
            name="shipping_item_price_source_check",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shipping_case_id: Mapped[int] = mapped_column(
        ForeignKey("ctip.shipping_case.id", ondelete="CASCADE"), nullable=False
    )
    firebird_warehouse_item_id: Mapped[int] = mapped_column(Integer, nullable=False)
    warehouse_id: Mapped[int] = mapped_column(Integer, nullable=False)
    item_index: Mapped[str | None] = mapped_column(Text, nullable=True)
    item_name: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[str] = mapped_column(Text, nullable=False, default="szt.")
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    price_net: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    catalog_price_net: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, default=0, server_default="0"
    )
    purchase_price_net: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    price_source: Mapped[str] = mapped_column(
        Text, nullable=False, default="sale", server_default="sale"
    )
    vat_rate: Mapped[Decimal] = mapped_column(Numeric(8, 3), nullable=False)
    allow_negative_stock: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    firebird_position_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )

    shipping_case: Mapped[ShippingCase] = relationship(back_populates="items")


class ShippingDayClose(Base):
    """Jednorazowe zamknięcie dnia po przekazaniu przesyłek kurierowi."""

    __tablename__ = "shipping_day_close"
    __table_args__ = (
        CheckConstraint(
            "status in ('processing','completed','partial','failed')",
            name="shipping_day_close_status_check",
        ),
        UniqueConstraint("business_date", name="uq_shipping_day_close_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="processing")
    shipment_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    closed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    closed_by: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.admin_user.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ShippingShipment(Base):
    """Przesyłka kurierska wraz z etykietą i etapami uzgodnienia systemów."""

    __tablename__ = "shipping_shipment"
    __table_args__ = (
        CheckConstraint(
            "status in ('processing','label_ready','handed_over','closed','failed',"
            "'reconcile_required')",
            name="shipping_shipment_status_check",
        ),
        UniqueConstraint("shipping_case_id", name="uq_shipping_shipment_case"),
        UniqueConstraint("idempotency_key", name="uq_shipping_shipment_idempotency"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shipping_case_id: Mapped[int] = mapped_column(
        ForeignKey("ctip.shipping_case.id", ondelete="CASCADE"), nullable=False
    )
    day_close_id: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.shipping_day_close.id", ondelete="SET NULL"), nullable=True
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False, default="dpd")
    provider_mode: Mapped[str] = mapped_column(Text, nullable=False)
    provider_shipment_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    tracking_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="processing")
    label_content: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    label_content_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    label_format: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_request: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    provider_response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    firebird_status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    firebird_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    firebird_rw_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    firebird_rw_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    firebird_wz_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    firebird_wz_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    firebird_invoice_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    firebird_invoice_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    notification_sms_status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    notification_email_status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    notification_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.admin_user.id", ondelete="SET NULL"), nullable=True
    )
    closed_by: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.admin_user.id", ondelete="SET NULL"), nullable=True
    )
    archive_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    archive_search_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    handed_over_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    shipping_case: Mapped[ShippingCase] = relationship(back_populates="shipment")


class ShippingEvent(Base):
    """Niemodyfikowalny dziennik operacji i błędów procesu wysyłki."""

    __tablename__ = "shipping_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shipping_case_id: Mapped[int] = mapped_column(
        ForeignKey("ctip.shipping_case.id", ondelete="CASCADE"), nullable=False
    )
    shipment_id: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.shipping_shipment.id", ondelete="CASCADE"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.admin_user.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )


class ShippingTrackingParcel(Base):
    """Bieżący stan listu przewozowego ustalony ze zdarzeń DPD InfoServices."""

    __tablename__ = "shipping_tracking_parcel"
    __table_args__ = (
        CheckConstraint(
            "status_category in ("
            "'registered','in_transit','out_for_delivery','pickup_ready','delivered',"
            "'undelivered','redirected','returning','critical','other'"
            ")",
            name="shipping_tracking_parcel_category_check",
        ),
        UniqueConstraint("provider", "waybill", name="uq_shipping_tracking_parcel_waybill"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False, default="dpd")
    waybill: Mapped[str] = mapped_column(Text, nullable=False)
    source_channel: Mapped[str] = mapped_column(Text, nullable=False)
    latest_business_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    latest_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    latest_event_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    latest_depot: Mapped[str | None] = mapped_column(Text, nullable=True)
    latest_depot_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    latest_country: Mapped[str | None] = mapped_column(Text, nullable=True)
    replacement_waybill: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_category: Mapped[str] = mapped_column(Text, nullable=False, default="other")
    status_label: Mapped[str] = mapped_column(Text, nullable=False, default="Inny status DPD")
    is_terminal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    requires_attention: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    first_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )

    events: Mapped[list[ShippingTrackingEvent]] = relationship(
        back_populates="parcel",
        cascade="all, delete-orphan",
    )


class ShippingTrackingEvent(Base):
    """Znormalizowane i idempotentne zdarzenie listu przewozowego z DPD."""

    __tablename__ = "shipping_tracking_event"
    __table_args__ = (
        CheckConstraint(
            "operation_type in ('INSERT','CANCEL')",
            name="shipping_tracking_event_operation_check",
        ),
        UniqueConstraint("source_event_key", name="uq_shipping_tracking_event_source_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    parcel_id: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.shipping_tracking_parcel.id", ondelete="CASCADE"), nullable=True
    )
    source_event_key: Mapped[str] = mapped_column(Text, nullable=False)
    waybill: Mapped[str | None] = mapped_column(Text, nullable=True)
    dpd_event_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    object_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    business_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    operation_type: Mapped[str] = mapped_column(Text, nullable=False, default="INSERT")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    depot: Mapped[str | None] = mapped_column(Text, nullable=True)
    depot_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    country: Mapped[str | None] = mapped_column(Text, nullable=True)
    package_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    parcel_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_data: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    is_cancelled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )

    parcel: Mapped[ShippingTrackingParcel | None] = relationship(back_populates="events")


class ShippingTrackingSyncRun(Base):
    """Rejestr wykonania synchronizacji kanału DPD InfoServices."""

    __tablename__ = "shipping_tracking_sync_run"
    __table_args__ = (
        CheckConstraint(
            "trigger_type in ('scheduler','manual','backfill')",
            name="shipping_tracking_sync_run_trigger_check",
        ),
        CheckConstraint(
            "status in ('processing','success','partial','failed')",
            name="shipping_tracking_sync_run_status_check",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_channel: Mapped[str] = mapped_column(Text, nullable=False)
    trigger_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="processing")
    fetched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    inserted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cancelled_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    batch_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confirm_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    acknowledgement_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    triggered_by: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.admin_user.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


Index("idx_shipping_case_status_updated", ShippingCase.status, ShippingCase.updated_at.desc())
Index(
    "idx_shipping_shipment_status_created",
    ShippingShipment.status,
    ShippingShipment.created_at.desc(),
)
Index(
    "idx_shipping_shipment_archive_closed",
    ShippingShipment.status,
    ShippingShipment.closed_at.desc(),
    ShippingShipment.id.desc(),
)
Index(
    "idx_shipping_shipment_archive_operator",
    ShippingShipment.closed_by,
    ShippingShipment.closed_at.desc(),
)
Index(
    "idx_shipping_shipment_provider_tracking",
    ShippingShipment.provider,
    ShippingShipment.tracking_number,
)
Index(
    "idx_shipping_event_case_created",
    ShippingEvent.shipping_case_id,
    ShippingEvent.created_at.desc(),
)
Index(
    "idx_shipping_tracking_parcel_status_event",
    ShippingTrackingParcel.status_category,
    ShippingTrackingParcel.latest_event_time.desc(),
)
Index(
    "idx_shipping_tracking_parcel_attention_event",
    ShippingTrackingParcel.requires_attention,
    ShippingTrackingParcel.latest_event_time.desc(),
)
Index(
    "idx_shipping_tracking_event_parcel_time",
    ShippingTrackingEvent.parcel_id,
    ShippingTrackingEvent.event_time.desc(),
    ShippingTrackingEvent.id.desc(),
)
Index(
    "idx_shipping_tracking_event_object",
    ShippingTrackingEvent.object_id,
)
Index(
    "idx_shipping_tracking_sync_started",
    ShippingTrackingSyncRun.started_at.desc(),
)
__all__ = [
    "ShippingAddress",
    "ShippingCase",
    "ShippingConsumableCompatibility",
    "ShippingDayClose",
    "ShippingEvent",
    "ShippingItem",
    "ShippingShipment",
    "ShippingTrackingEvent",
    "ShippingTrackingParcel",
    "ShippingTrackingSyncRun",
]
