"""Katalog nominalnych wydajności, dowodów źródłowych i historii korekt tonerów."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class TonerYield(Base):
    """Wartość operacyjna kartoteki MS, niezależna od wariantów i danych źródłowych."""

    __tablename__ = "shipping_toner_yield"
    __table_args__ = (
        CheckConstraint("status IN ('confirmed','estimated','missing')", name="toner_yield_status"),
        CheckConstraint("scope IN ('active','inactive','review')", name="toner_yield_scope"),
        CheckConstraint(
            "(status = 'missing' AND pages IS NULL) OR (status <> 'missing' AND pages IS NOT NULL AND pages > 0)",
            name="toner_yield_pages",
        ),
    )

    item_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    warehouse_id: Mapped[int] = mapped_column(Integer, nullable=False)
    item_index: Mapped[str] = mapped_column(Text, default="", nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    brand: Mapped[str] = mapped_column(Text, default="", nullable=False)
    supplier: Mapped[str] = mapped_column(Text, default="", nullable=False)
    sku: Mapped[str] = mapped_column(Text, default="", nullable=False)
    ean: Mapped[str] = mapped_column(Text, default="", nullable=False)
    color: Mapped[str] = mapped_column(Text, default="unknown", nullable=False)
    kind: Mapped[str] = mapped_column(Text, default="unknown", nullable=False)
    stock: Mapped[float] = mapped_column(Numeric(16, 4), default=0, nullable=False)
    models: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    scope: Mapped[str] = mapped_column(Text, default="review", nullable=False, index=True)
    scope_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    scope_review: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    excluded_model_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    pages: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, default="missing", nullable=False)
    source: Mapped[str] = mapped_column(Text, default="", nullable=False)
    basis: Mapped[str] = mapped_column(Text, default="", nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    manual_override: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TonerYieldEvidence(Base):
    """Niezmienny wariant lub dokument źródłowy, identyfikowany skrótem treści."""

    __tablename__ = "shipping_toner_yield_evidence"
    __table_args__ = (UniqueConstraint("item_id", "fingerprint", name="uq_toner_yield_evidence"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(
        ForeignKey("ctip.shipping_toner_yield.item_id"), index=True
    )
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TonerYieldChange(Base):
    """Transakcyjna historia wartości wraz z autorem i przyczyną zmiany."""

    __tablename__ = "shipping_toner_yield_change"
    __table_args__ = (UniqueConstraint("item_id", "revision", name="uq_toner_yield_revision"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(
        ForeignKey("ctip.shipping_toner_yield.item_id"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.admin_user.id", ondelete="SET NULL")
    )
    actor_label: Mapped[str] = mapped_column(Text, nullable=False)
    before: Mapped[dict] = mapped_column(JSON, nullable=False)
    after: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
