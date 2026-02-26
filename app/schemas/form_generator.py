"""Schematy danych dla generatora formularzy."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

PHONE_PATTERN = r"^[0-9+\s\-()]+$"

FORM_STATUS = Literal["GENERATED", "DISPATCHED", "SUBMITTED", "EXPIRED"]


class FormRequestCreate(BaseModel):
    """Żądanie wygenerowania bezpiecznego formularza dla klienta."""

    customer_name: str = Field(min_length=2, max_length=160)
    customer_email: EmailStr
    customer_phone: str = Field(min_length=6, max_length=32, pattern=PHONE_PATTERN)

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
    customer_email: EmailStr
    customer_phone: str
    created_by_name: str | None = None
    status: FORM_STATUS
    token_expires_at: datetime
    submitted_at: datetime | None = None
    created_at: datetime
    sms_status: str | None = None
    email_status: str | None = None

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
    personal_id: str = Field(min_length=4, max_length=64)
    document_type: str = Field(min_length=2, max_length=120)
    document_number: str = Field(min_length=2, max_length=120)
    document_issue_validity: str = Field(min_length=2, max_length=200)

    @field_validator(
        "first_name",
        "last_name",
        "personal_id",
        "document_type",
        "document_number",
        "document_issue_validity",
        mode="before",
    )
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped
        raise ValueError("Pole jest wymagane.")


class PublicFormSubmission(BaseModel):
    """Dane wprowadzane przez klienta w publicznym formularzu."""

    company_name: str = Field(min_length=2, max_length=200)
    company_nip: str = Field(min_length=8, max_length=20)
    company_phone: str = Field(min_length=6, max_length=32, pattern=PHONE_PATTERN)
    company_email: EmailStr
    billing_email: EmailStr | None = None
    registered_address: str = Field(min_length=4, max_length=300)
    correspondence_address: str | None = Field(default=None, max_length=300)
    representatives: list[PublicFormRepresentative] = Field(min_length=1, max_length=10)
    consent: bool
    website: str | None = Field(default=None, max_length=120)

    @field_validator(
        "company_name",
        "company_nip",
        "company_phone",
        "company_email",
        "registered_address",
        mode="before",
    )
    @classmethod
    def _strip_required(cls, value: str) -> str:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped
        raise ValueError("Pole jest wymagane.")

    @field_validator("billing_email", "correspondence_address", "website", mode="before")
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
