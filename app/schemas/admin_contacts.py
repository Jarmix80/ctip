"""Schematy Pydantic wykorzystywane w module zarządzania kontaktami."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator


class _AdminContactBase(BaseModel):
    number: str = Field(min_length=4, max_length=32)
    ext: str | None = Field(default=None, max_length=16)
    firebird_id: str | None = Field(default=None, max_length=64)
    first_name: str | None = Field(default=None, max_length=120)
    last_name: str | None = Field(default=None, max_length=120)
    company: str | None = Field(default=None, max_length=160)
    email: EmailStr | None = None
    nip: str | None = Field(default=None, max_length=20)
    notes: str | None = Field(default=None, max_length=2000)
    source: str = Field(default="manual", max_length=32)

    @field_validator(
        "ext",
        "firebird_id",
        "first_name",
        "last_name",
        "company",
        "nip",
        "notes",
        "source",
        mode="before",
    )
    @classmethod
    def _strip_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("number", mode="before")
    @classmethod
    def _normalize_number(cls, value: str) -> str:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                raise ValueError("Numer telefonu nie może być pusty.")
            return stripped
        raise ValueError("Nieprawidłowy numer telefonu.")


class AdminContactCreate(_AdminContactBase):
    """Payload dodawania nowego kontaktu."""


class AdminContactUpdate(_AdminContactBase):
    """Payload aktualizacji istniejącego kontaktu."""


class AdminContactSummary(BaseModel):
    """Widok listy kontaktów w panelu administratora."""

    id: int
    number: str
    ext: str | None
    firebird_id: str | None
    first_name: str | None
    last_name: str | None
    company: str | None
    email: str | None
    nip: str | None
    notes: str | None
    source: str
    created_at: datetime
    updated_at: datetime


class AdminContactListResponse(BaseModel):
    """Odpowiedź listy kontaktów z paginacją w przyszłości."""

    items: list[AdminContactSummary]


class AdminContactDetail(AdminContactSummary):
    """Widok szczegółowy kontaktu."""
