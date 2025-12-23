"""Schematy obsługi wiadomości SMS."""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SMS_E164_PATTERN = r"^\+[1-9]\d{7,14}$"
_SMS_E164_REGEX = re.compile(SMS_E164_PATTERN)


def normalize_sms_destination(value: str | None) -> str:
    """Normalizuje numer docelowy SMS do E.164 (00/0/+0 i prefiks wyjscia 0)."""
    if value is None:
        raise ValueError("Numer docelowy jest wymagany.")
    raw = value.strip() if isinstance(value, str) else str(value).strip()
    if not raw:
        raise ValueError("Numer docelowy jest wymagany.")
    digits = re.sub(r"\D", "", raw)
    if not digits:
        raise ValueError("Numer docelowy jest nieprawidłowy.")
    if digits.startswith("000"):
        digits = digits[1:]
    if digits.startswith("0") and not digits.startswith("00") and len(digits) >= 10:
        digits = digits[1:]
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 9:
        digits = f"48{digits}"
    normalized = f"+{digits}"
    if not _SMS_E164_REGEX.fullmatch(normalized):
        raise ValueError("Numer docelowy musi mieć format E.164 (np. +48123123123).")
    return normalized


class SmsTemplateCreate(BaseModel):
    """Definicja nowego szablonu wiadomości."""

    name: str = Field(..., min_length=1, max_length=80)
    body: str = Field(..., min_length=1, max_length=640)
    scope: str | None = Field("user", pattern="^(global|user|contact)$")
    is_active: bool = True


class SmsTemplateRead(BaseModel):
    """Szablon dostępny w panelu."""

    id: int
    name: str
    body: str
    scope: str
    owner_id: int | None
    is_active: bool
    created_by: int | None
    updated_by: int | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SmsHistoryItem(BaseModel):
    """Wpis w historii SMS powiązanych z połączeniem lub numerem."""

    id: int
    created_at: datetime
    status: str
    provider_status: str | None
    sender: str | None
    template_id: int | None
    template_name: str | None
    text: str

    model_config = ConfigDict(from_attributes=True)


class SmsSendRequest(BaseModel):
    """Żądanie wysłania SMS z poziomu UI (numer docelowy normalizowany do E.164)."""

    dest: str = Field(
        ...,
        description="Docelowy numer MSISDN",
        pattern=SMS_E164_PATTERN,
    )
    text: str | None = Field(None, min_length=1, max_length=480)
    call_id: int | None = Field(None, description="Powiązane połączenie CTIP")
    template_id: int | None = Field(None, description="Szablon użyty do wysyłki")
    origin: str = Field(default="ui")

    model_config = ConfigDict(extra="forbid")

    @field_validator("dest", mode="before")
    @classmethod
    def _normalize_dest(cls, value: str) -> str:
        return normalize_sms_destination(value)

    @model_validator(mode="after")
    def validate_content(self) -> SmsSendRequest:
        if not self.text and not self.template_id:
            raise ValueError("Wymagane jest podanie treści lub szablonu.")
        return self


class SmsAccountSummary(BaseModel):
    """Informacje o stanie konta SMS."""

    default_sender: str
    total_sent: int
    pending: int
    failed: int
    last_template_update: datetime | None


# Zachowanie kompatybilności z dotychczasową nazwą
SmsCreate = SmsSendRequest
