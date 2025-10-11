"""Schematy danych kontaktowych."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ContactDeviceSchema(BaseModel):
    """Urządzenie przypisane do kontaktu."""

    id: int
    device_name: str | None
    serial_number: str | None
    location: str | None
    notes: str | None
    created_at: datetime


class ContactSchema(BaseModel):
    """Dane kontaktowe powiązane z numerem telefonu."""

    id: int
    number: str
    ext: str | None
    first_name: str | None
    last_name: str | None
    company: str | None
    nip: str | None
    email: str | None
    notes: str | None
    source: str
    created_at: datetime
    updated_at: datetime
    devices: list[ContactDeviceSchema] = Field(default_factory=list)
