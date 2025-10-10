"""Modele kartoteki kontaktów oraz urządzeń powiązanych."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class Contact(Base):
    """Informacje o kontrahencie/przypisanym numerze telefonu."""

    __tablename__ = "contact"

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(Text, nullable=False)
    ext: Mapped[str | None] = mapped_column(Text)
    first_name: Mapped[str | None] = mapped_column(Text)
    last_name: Mapped[str | None] = mapped_column(Text)
    company: Mapped[str | None] = mapped_column(Text)
    nip: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, default="manual")
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    devices: Mapped[list[ContactDevice]] = relationship(
        back_populates="contact", cascade="all, delete-orphan", lazy="selectin"
    )


class ContactDevice(Base):
    """Urządzenia wynajmowane przez klienta (do powiązań z zapytaniami o liczniki)."""

    __tablename__ = "contact_device"

    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("ctip.contact.id", ondelete="CASCADE"))
    device_name: Mapped[str | None] = mapped_column(Text)
    serial_number: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    contact: Mapped[Contact] = relationship(back_populates="devices")


Index("idx_contact_number", Contact.number)
Index("idx_contact_ext", Contact.ext)
