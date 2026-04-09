"""Schematy danych dla generatora formularzy."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

PHONE_PATTERN = r"^[0-9+\s\-()]+$"

FORM_STATUS = Literal["GENERATED", "DISPATCHED", "SUBMITTED", "EXPIRED"]


def _normalize_date_text(value: str, *, field_name: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("Pole jest wymagane.")
    for date_format in ("%d:%m:%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(stripped, date_format)
            return parsed.strftime("%d-%m-%Y")
        except ValueError:
            continue
    raise ValueError(f"Pole '{field_name}' ma nieprawidłowy format daty (dd-mm-rrrr).")


def _validate_pesel_checksum(pesel: str) -> bool:
    weights = (1, 3, 7, 9, 1, 3, 7, 9, 1, 3)
    checksum = sum(int(digit) * weight for digit, weight in zip(pesel[:10], weights, strict=True))
    control = (10 - (checksum % 10)) % 10
    return control == int(pesel[-1])


class FormRequestCreate(BaseModel):
    """Żądanie wygenerowania bezpiecznego formularza dla klienta."""

    customer_name: str = Field(min_length=2, max_length=160)
    customer_email: EmailStr
    customer_phone: str = Field(min_length=6, max_length=32, pattern=PHONE_PATTERN)
    expires_on: date | None = None

    @field_validator("customer_name", "customer_phone", mode="before")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped
        raise ValueError("Pole jest wymagane.")


class FormRequestSummary(BaseModel):
    """Skrócony widok formularza wygenerowanego przez handlowca."""

    id: int
    customer_name: str
    customer_email: str
    customer_phone: str
    created_by_name: str | None = None
    status: FORM_STATUS
    token_expires_at: datetime
    submitted_at: datetime | None = None
    created_at: datetime
    sms_status: str | None = None
    email_status: str | None = None
    ms_status: str | None = None

    model_config = ConfigDict(from_attributes=True)


class FormRequestListResponse(BaseModel):
    """Lista wygenerowanych formularzy."""

    items: list[FormRequestSummary]


class FormRequestCreateResponse(BaseModel):
    """Odpowiedź po wygenerowaniu nowego linku formularza."""

    item: FormRequestSummary
    form_url: str
    sms_queued: bool
    email_sent: bool
    warnings: list[str] = Field(default_factory=list)


class FormRequestDetailResponse(BaseModel):
    """Szczegóły pojedynczego formularza dla widoku operacyjnego."""

    item: FormRequestSummary
    status_message: str
    submitted_payload: dict[str, object] | None = None
    submitted_meta: dict[str, str | None] | None = None


class PublicFormRepresentative(BaseModel):
    """Dane reprezentanta firmy podpisującego wniosko-umowę."""

    first_name: str = Field(min_length=2, max_length=120)
    last_name: str = Field(min_length=2, max_length=120)
    representative_email: EmailStr
    representative_phone: str = Field(min_length=6, max_length=32, pattern=PHONE_PATTERN)
    pesel: str = Field(min_length=11, max_length=11)
    birth_date: str = Field(min_length=10, max_length=10)
    document_type: str = Field(min_length=2, max_length=120)
    document_number: str = Field(min_length=2, max_length=120)
    document_issue_date: str = Field(min_length=10, max_length=10)
    document_expiry_date: str = Field(min_length=10, max_length=10)

    @field_validator(
        "first_name",
        "last_name",
        "representative_email",
        "representative_phone",
        "document_type",
        "document_number",
        mode="before",
    )
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped
        raise ValueError("Pole jest wymagane.")

    @field_validator("pesel", mode="before")
    @classmethod
    def _validate_pesel(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("Pole jest wymagane.")
        normalized = re.sub(r"\s+", "", value)
        if not re.fullmatch(r"\d{11}", normalized):
            raise ValueError("PESEL musi składać się z 11 cyfr.")
        if not _validate_pesel_checksum(normalized):
            raise ValueError("PESEL ma nieprawidłową sumę kontrolną.")
        return normalized

    @field_validator("birth_date", "document_issue_date", "document_expiry_date", mode="before")
    @classmethod
    def _normalize_dates(cls, value: str, info) -> str:
        if not isinstance(value, str):
            raise ValueError("Pole jest wymagane.")
        return _normalize_date_text(value, field_name=info.field_name)


class PublicFormSubmission(BaseModel):
    """Dane wprowadzane przez klienta w publicznym formularzu."""

    company_name: str = Field(min_length=2, max_length=200)
    company_nip: str = Field(min_length=8, max_length=20)
    company_phone: str = Field(min_length=6, max_length=32, pattern=PHONE_PATTERN)
    company_email: EmailStr
    billing_email: EmailStr
    registered_street: str = Field(min_length=2, max_length=160)
    registered_building_no: str = Field(min_length=1, max_length=20)
    registered_apartment_no: str | None = Field(default=None, max_length=20)
    registered_postal_code: str = Field(min_length=2, max_length=20)
    registered_city: str = Field(min_length=2, max_length=120)
    correspondence_same_as_registered: bool = False
    correspondence_street: str = Field(min_length=2, max_length=160)
    correspondence_building_no: str = Field(min_length=1, max_length=20)
    correspondence_apartment_no: str | None = Field(default=None, max_length=20)
    correspondence_postal_code: str = Field(min_length=2, max_length=20)
    correspondence_city: str = Field(min_length=2, max_length=120)
    representatives: list[PublicFormRepresentative] = Field(min_length=1, max_length=10)
    consent: bool
    website: str | None = Field(default=None, max_length=120)

    @field_validator(
        "company_name",
        "company_nip",
        "company_phone",
        "company_email",
        "billing_email",
        "registered_street",
        "registered_building_no",
        "registered_postal_code",
        "registered_city",
        "correspondence_street",
        "correspondence_building_no",
        "correspondence_postal_code",
        "correspondence_city",
        mode="before",
    )
    @classmethod
    def _strip_required(cls, value: str) -> str:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped
        raise ValueError("Pole jest wymagane.")

    @field_validator(
        "registered_apartment_no",
        "correspondence_apartment_no",
        "website",
        mode="before",
    )
    @classmethod
    def _strip_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("consent")
    @classmethod
    def _validate_consent(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Wymagana jest zgoda na przetwarzanie danych.")
        return value

    @field_validator("website")
    @classmethod
    def _validate_honeypot(cls, value: str | None) -> str | None:
        if value:
            raise ValueError("Nieprawidłowe dane formularza.")
        return value
