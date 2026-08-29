"""Modele ORM modułu wysyłek części i materiałów eksploatacyjnych."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
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
    """Potwierdzone mapowanie modelu urządzenia na kartotekę magazynową tonera."""

    __tablename__ = "shipping_consumable_compatibility"
    __table_args__ = (
        UniqueConstraint(
            "firebird_model_id",
            "firebird_warehouse_item_id",
            name="uq_shipping_compatibility_model_item",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    firebird_model_id: Mapped[int] = mapped_column(Integer, nullable=False)
    firebird_warehouse_item_id: Mapped[int] = mapped_column(Integer, nullable=False)
    model_label: Mapped[str] = mapped_column(Text, nullable=False)
    item_index: Mapped[str | None] = mapped_column(Text, nullable=True)
    item_name: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_by: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.admin_user.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )


class ShippingCase(Base):
    """Stan obsługi pojedynczego zlecenia dowozu materiałów z Firebirda."""

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
    status: Mapped[str] = mapped_column(Text, nullable=False, default="review_pending")
    address_id: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.shipping_address.id", ondelete="SET NULL"), nullable=True
    )
    address_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    source_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    weight_kg: Mapped[Decimal] = mapped_column(Numeric(8, 3), nullable=False)
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
    """Wybrana pozycja magazynowa przypisana do przygotowywanej przesyłki."""

    __tablename__ = "shipping_item"
    __table_args__ = (
        UniqueConstraint(
            "shipping_case_id",
            "firebird_warehouse_item_id",
            name="uq_shipping_item_case_warehouse",
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
    purchase_price_net: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    vat_rate: Mapped[Decimal] = mapped_column(Numeric(8, 3), nullable=False)
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
    notification_sms_status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    notification_email_status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    notification_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.admin_user.id", ondelete="SET NULL"), nullable=True
    )
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


Index("idx_shipping_case_status_updated", ShippingCase.status, ShippingCase.updated_at.desc())
Index(
    "idx_shipping_shipment_status_created",
    ShippingShipment.status,
    ShippingShipment.created_at.desc(),
)
Index(
    "idx_shipping_event_case_created",
    ShippingEvent.shipping_case_id,
    ShippingEvent.created_at.desc(),
)
Index(
    "idx_shipping_compatibility_model",
    ShippingConsumableCompatibility.firebird_model_id,
)


__all__ = [
    "ShippingAddress",
    "ShippingCase",
    "ShippingConsumableCompatibility",
    "ShippingDayClose",
    "ShippingEvent",
    "ShippingItem",
    "ShippingShipment",
]
