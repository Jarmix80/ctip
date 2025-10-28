"""Schematy danych wykorzystywane w panelu operatora."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator


class OperatorContactSummary(BaseModel):
    """Skrócona informacja o kontakcie powiązanym z numerem."""

    id: int | None = None
    number: str
    ext: str | None = None
    firebird_id: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    company: str | None = None
    email: EmailStr | None = None
    nip: str | None = None
    notes: str | None = None


class OperatorCallListItem(BaseModel):
    """Element listy połączeń w widoku operatora."""

    id: int
    started_at: datetime
    direction: str
    ext: str
    number: str | None = None
    disposition: str
    duration_s: int | None = None
    contact: OperatorContactSummary | None = None


class OperatorCallEvent(BaseModel):
    """Pojedyncze zdarzenie CTIP w osi czasu."""

    ts: datetime
    typ: str
    ext: str | None = None
    number: str | None = None
    payload: str | None = None


class OperatorSmsItem(BaseModel):
    """Wpis historii SMS."""

    id: int
    created_at: datetime
    dest: str
    text: str
    status: str
    origin: str | None = None
    provider_status: str | None = None
    provider_message_id: str | None = None
    error_msg: str | None = None


class OperatorCallDetail(BaseModel):
    """Szczegół połączenia wraz z osią czasu i historią SMS."""

    call: OperatorCallListItem
    events: list[OperatorCallEvent]
    sms_history: list[OperatorSmsItem]
    contact: OperatorContactSummary | None = None


class OperatorContactDetail(OperatorContactSummary):
    """Pełne dane kontaktu (z informacją o źródle i znacznikach czasu)."""

    source: str
    created_at: datetime
    updated_at: datetime


class OperatorUserInfo(BaseModel):
    """Dane zalogowanego operatora."""

    id: int
    email: EmailStr
    first_name: str | None = None
    last_name: str | None = None
    role: str


class OperatorSendSmsRequest(BaseModel):
    """Żądanie wysyłki SMS w panelu operatora."""

    dest: str = Field(..., pattern=r"^\+[1-9]\d{7,14}$")
    text: str = Field(..., min_length=1, max_length=480)
    call_id: int | None = None
    origin: str | None = Field(default="operator")


class OperatorSendSmsResponse(BaseModel):
    """Odpowiedź po zapisaniu wiadomości do kolejki."""

    sms: OperatorSmsItem


class OperatorLoginRequest(BaseModel):
    """Żądanie logowania operatora."""

    email: EmailStr
    password: str = Field(min_length=1)
    remember_me: bool = False


class OperatorProfile(BaseModel):
    """Widok danych profilu operatora."""

    email: EmailStr
    first_name: str | None = None
    last_name: str | None = None
    internal_ext: str | None = None
    mobile_phone: str | None = None
    role: str


class OperatorProfileUpdate(BaseModel):
    """Aktualizacja danych operatora."""

    email: EmailStr
    first_name: str | None = Field(default=None, max_length=120)
    last_name: str | None = Field(default=None, max_length=120)
    internal_ext: str | None = Field(default=None, max_length=16)
    mobile_phone: str | None = Field(default=None, max_length=32)

    @field_validator("first_name", "last_name", "internal_ext", "mobile_phone", mode="before")
    @classmethod
    def _strip_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class OperatorPasswordChangeRequest(BaseModel):
    """Żądanie zmiany hasła przez operatora."""

    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8, max_length=128)


class OperatorSmsTemplateBase(BaseModel):
    """Wspólne pola formularzy szablonów SMS operatora."""

    name: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=480)
    is_active: bool = True

    @field_validator("name", "body", mode="before")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                raise ValueError("Pole nie może być puste.")
            return stripped
        raise ValueError("Pole wymagane.")


class OperatorSmsTemplateCreate(OperatorSmsTemplateBase):
    """Dodawanie szablonu SMS."""


class OperatorSmsTemplateUpdate(OperatorSmsTemplateBase):
    """Aktualizacja szablonu SMS."""


class OperatorSmsTemplateRead(OperatorSmsTemplateBase):
    """Widok szablonu SMS w panelu operatora."""

    id: int
    scope: str
    owner_id: int | None
    editable: bool
    created_at: datetime
    updated_at: datetime


class OperatorContactUpsert(BaseModel):
    """Payload dodawania lub edycji kontaktu z poziomu operatora."""

    number: str = Field(min_length=4, max_length=32)
    ext: str | None = Field(default=None, max_length=16)
    firebird_id: str | None = Field(default=None, max_length=64)
    first_name: str | None = Field(default=None, max_length=120)
    last_name: str | None = Field(default=None, max_length=120)
    company: str | None = Field(default=None, max_length=160)
    email: EmailStr | None = None
    nip: str | None = Field(default=None, max_length=20)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator(
        "ext",
        "firebird_id",
        "first_name",
        "last_name",
        "company",
        "nip",
        "notes",
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
    def _strip_number(cls, value: str) -> str:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                raise ValueError("Numer telefonu nie może być pusty.")
            return stripped
        raise ValueError("Nieprawidłowy numer telefonu.")


class OperatorStats(BaseModel):
    """Zbiorcze statystyki SMS w panelu operatora."""

    sms_today: int
    sms_month: int
