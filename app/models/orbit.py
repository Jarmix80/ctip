"""Odtwarzalny model raportowy ORBIT bez kopii dokumentów źródłowych."""

from datetime import datetime

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class OrbitDevice(Base):
    """Trwała tożsamość urządzenia ze statusem aktywności kontrolowanym przez bazę."""

    __tablename__ = "shipping_orbit_device"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','suspended','scrapped','review')", name="orbit_device_status"
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    ms_machine_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    serial: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    customer: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    report: Mapped[dict] = mapped_column(JSON, nullable=False)
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False, default="")
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OrbitEvent(Base):
    """Bieżący fakt z odnośnikiem do niezmiennego dowodu telemetrii."""

    __tablename__ = "shipping_orbit_event"
    __table_args__ = (
        Index("idx_orbit_device_time", "device_id", "observed_at", "id"),
        Index("idx_orbit_device_contract", "device_id", "contract_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    device_id: Mapped[str] = mapped_column(
        ForeignKey("ctip.shipping_orbit_device.id"), nullable=False
    )
    record_id: Mapped[str] = mapped_column(ForeignKey("ctip.telemetry_record.id"), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    time_precision: Mapped[str] = mapped_column(Text, nullable=False)
    contract_id: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    finance: Mapped[dict] = mapped_column(JSON, nullable=False)
    current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class OrbitRun(Base):
    """Ostatni stan workera i bezpieczny kod błędu bez treści wyjątków źródła."""

    __tablename__ = "shipping_orbit_run"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    counts: Mapped[dict] = mapped_column(JSON, nullable=False)
    error_code: Mapped[str | None] = mapped_column(Text)


class OrbitEvidence(Base):
    """Wiele oryginałów jednego zdarzenia bez powielania treści i ilości."""

    __tablename__ = "shipping_orbit_evidence"

    event_id: Mapped[str] = mapped_column(
        ForeignKey("ctip.shipping_orbit_event.id"), primary_key=True
    )
    record_id: Mapped[str] = mapped_column(ForeignKey("ctip.telemetry_record.id"), primary_key=True)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class OrbitPolicy(Base):
    """Częściowe nadpisanie polityki globalnej, klienta lub urządzenia i koloru."""

    __tablename__ = "shipping_orbit_policy"
    __table_args__ = (
        CheckConstraint("scope IN ('global','customer','device')", name="orbit_policy_scope"),
        CheckConstraint(
            "color IN ('all','black','cyan','magenta','yellow')", name="orbit_policy_color"
        ),
    )

    scope: Mapped[str] = mapped_column(Text, primary_key=True)
    scope_id: Mapped[str] = mapped_column(Text, primary_key=True)
    color: Mapped[str] = mapped_column(Text, primary_key=True)
    values: Mapped[dict] = mapped_column("values", JSON, nullable=False, quote=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("ctip.admin_user.id"))
