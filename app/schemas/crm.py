"""Kontrakty API Centrum Obsługi i laboratorium kanałów klienta."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

CrmQueue = Literal["sales", "service_it", "contracts", "other"]
CrmStatus = Literal["new", "active", "transferred", "done", "archived"]
CrmPriority = Literal["low", "normal", "high"]
ChatCaseCategory = Literal[
    "sales",
    "service",
    "accounting",
    "other",
    "contracts_settlements",
]
ChatCustomerMatchStatus = Literal[
    "exact",
    "unique",
    "ambiguous",
    "not_found",
    "unavailable",
    "not_applicable",
]
ChatSmsVerificationStatus = Literal[
    "sms_verified_known",
    "sms_verified_self_declared",
]
_CHAT_REF_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
CrmSource = Literal[
    "chat",
    "voice",
    "form",
    "configurator",
    "email",
    "phone",
    "manual",
    "scenario",
]


class CrmContactInput(BaseModel):
    """Dane osoby kontaktowej przekazane przez kanał wejściowy."""

    name: str = Field(min_length=2, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    email: EmailStr | None = None


class CrmCompanyInput(BaseModel):
    """Minimalne dane firmy związanej ze sprawą."""

    name: str = Field(min_length=2, max_length=300)
    customer_ref: str | None = Field(default=None, max_length=128)


class CrmCaseCreateRequest(BaseModel):
    """Znormalizowane wejście sprawy z CHAT_KP, formularza lub panelu."""

    model_config = ConfigDict(extra="allow")

    external_ref: str | None = Field(default=None, max_length=160)
    case_ref: str | None = Field(default=None, max_length=160)
    conversation_ref: str | None = Field(default=None, max_length=160)
    channel: CrmSource = "manual"
    queue: CrmQueue | None = None
    category: str | None = Field(default=None, max_length=80)
    priority: CrmPriority = "normal"
    subject: str = Field(min_length=3, max_length=300)
    message: str | None = Field(default=None, max_length=12000)
    content: str | None = Field(default=None, max_length=12000)
    summary: str | None = Field(default=None, max_length=12000)
    company: CrmCompanyInput | None = None
    company_name: str | None = Field(default=None, max_length=300)
    customer_ref: str | None = Field(default=None, max_length=128)
    contact: CrmContactInput | None = None
    contact_name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    email: EmailStr | None = None
    source_detail: str | None = Field(default=None, max_length=300)
    source_url: str | None = Field(default=None, max_length=2000)
    identity_status: str | None = Field(default=None, max_length=80)
    device_label: str | None = Field(default=None, max_length=300)
    device_refs: list[str] | None = None
    device_serial_last4: str | None = Field(default=None, min_length=1, max_length=16)
    is_lab: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_required_variants(self) -> CrmCaseCreateRequest:
        if not (self.external_ref or self.case_ref or self.conversation_ref):
            raise ValueError("Wymagany jest external_ref, case_ref albo conversation_ref.")
        if not (self.message or self.content or self.summary):
            raise ValueError("Wymagana jest treść sprawy.")
        if not (self.company or self.company_name):
            raise ValueError("Wymagana jest nazwa firmy.")
        if not (self.contact or self.contact_name):
            raise ValueError("Wymagane są dane osoby kontaktowej.")
        return self

    @field_validator("category", mode="before")
    @classmethod
    def normalize_category(cls, value: Any) -> Any:
        return value.strip().lower() if isinstance(value, str) else value


class CrmCaseActionRequest(BaseModel):
    """Kontrolowana zmiana stanu sprawy wykonywana przez operatora."""

    action: Literal[
        "claim",
        "assign",
        "unassign",
        "note",
        "close",
        "reopen",
        "transfer_ms",
        "meter_update",
    ]
    declared_operator_id: int = Field(ge=1)
    owner_user_id: int | None = Field(default=None, ge=1)
    owner_name: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=4000)
    ms_order_ref: str | None = Field(default=None, max_length=100)
    meters: dict[Literal["bw", "color", "scan"], int | None] | None = None

    @model_validator(mode="after")
    def validate_action_payload(self) -> CrmCaseActionRequest:
        if self.action == "note" and not (self.note and len(self.note.strip()) >= 3):
            raise ValueError("Notatka musi mieć co najmniej 3 znaki.")
        if self.action == "assign" and not (self.owner_user_id or self.owner_name):
            raise ValueError("Wskaż użytkownika albo nazwę właściciela.")
        if self.action == "meter_update":
            values = (self.meters or {}).values()
            if not any(value is not None for value in values):
                raise ValueError("Wpisz co najmniej jeden licznik.")
        return self


class CrmOperatorResponse(BaseModel):
    id: int
    name: str
    email: str
    role: str
    phone_available: bool


class CrmCaseEventResponse(BaseModel):
    ref: str
    type: str
    title: str
    text: str | None
    actor: str | None
    payload: dict[str, Any] | None
    created_at: datetime


class CrmCaseResponse(BaseModel):
    ref: str
    external_ref: str
    conversation_ref: str | None
    source: CrmSource
    source_detail: str | None
    source_url: str | None
    queue: CrmQueue
    category: str
    status: CrmStatus
    priority: CrmPriority
    subject: str
    message: str
    company_name: str
    contact_name: str
    contact_phone: str | None
    contact_email: str | None
    customer_ref: str | None
    identity_status: str | None
    device_label: str | None
    device_refs: list[str] | None
    device_serial_last4: str | None
    owner_user_id: int | None
    owner_name: str | None
    declared_operator_id: int | None
    ms_order_ref: str | None
    is_lab: bool
    created_at: datetime
    updated_at: datetime
    first_claimed_at: datetime | None
    terminal_at: datetime | None
    archived_at: datetime | None
    retained_until: datetime
    events: list[CrmCaseEventResponse] = Field(default_factory=list)


class CrmCaseListResponse(BaseModel):
    items: list[CrmCaseResponse]
    total: int


class CrmCaseCreateResponse(BaseModel):
    case: CrmCaseResponse
    created: bool


class CrmLabResetRequest(BaseModel):
    declared_operator_id: int = Field(ge=1)
    reason: str = Field(min_length=10, max_length=500)


class CrmLabResetResponse(BaseModel):
    deleted_cases: int
    deleted_events: int


class CrmCapabilitiesResponse(BaseModel):
    """Kontrakt funkcji CTIP zgodny z klientem CHAT_KP."""

    service: Literal["ctip"] = "ctip"
    contract_version: Literal["1.0"] = "1.0"
    categories: list[
        Literal["sales", "service", "accounting", "other", "contracts_settlements"]
    ] = Field(
        default_factory=lambda: [
            "sales",
            "service",
            "accounting",
            "other",
            "contracts_settlements",
        ]
    )
    customer_resolution: Literal[True] = True
    sms_verification: Literal[True] = True
    masked_devices: Literal[True] = True
    idempotent_sms: Literal[True] = True
    idempotent_cases: Literal[True] = True


class ChatCustomerResolveRequest(BaseModel):
    """Minimalne dane rozpoznania firmy przekazywane przez CHAT_KP."""

    model_config = ConfigDict(extra="forbid")

    nip: str | None = None
    name: str | None = Field(default=None, min_length=2, max_length=200)


class ChatCustomerResolveResponse(BaseModel):
    """Bezpieczny wynik rozpoznania bez danych kontaktowych."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["exact", "unique", "ambiguous", "not_found"]
    candidate_count: int = Field(ge=0)
    customer_ref: str | None = Field(default=None, pattern=_CHAT_REF_PATTERN)
    company_name: str | None = Field(default=None, max_length=300)
    matched_by: Literal["nip", "name"] | None = None


class ChatSmsChallengeCreateRequest(BaseModel):
    """Żądanie testowego potwierdzenia numeru w bieżącej rozmowie."""

    model_config = ConfigDict(extra="forbid")

    phone: str = Field(min_length=8, max_length=24)
    customer_ref: str | None = Field(default=None, pattern=_CHAT_REF_PATTERN)


class ChatSmsChallengeResponse(BaseModel):
    """Identyfikator i termin ważności wyzwania bez ujawnienia kodu."""

    model_config = ConfigDict(extra="forbid")

    challenge_id: str = Field(pattern=_CHAT_REF_PATTERN)
    expires_at: datetime
    attempts_remaining: int = Field(ge=0)
    status: Literal["pending"] = "pending"


class ChatSmsChallengeVerifyRequest(BaseModel):
    """Sześciocyfrowy kod podany przez użytkownika."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^\d{6}$")


class ChatSmsChallengeVerifyResponse(BaseModel):
    """Wynik weryfikacji bez zwracania numeru telefonu."""

    model_config = ConfigDict(extra="forbid")

    challenge_id: str = Field(pattern=_CHAT_REF_PATTERN)
    status: Literal[
        "pending",
        "verified",
        "invalid_code",
        "expired",
        "attempts_exceeded",
    ]
    attempts_remaining: int = Field(ge=0)
    customer_ref: str | None = Field(default=None, pattern=_CHAT_REF_PATTERN)
    verification_status: ChatSmsVerificationStatus | None = None


class ChatMaskedDevicesRequest(BaseModel):
    """Dowód weryfikacji SMS wymagany przed odczytem urządzeń."""

    model_config = ConfigDict(extra="forbid")

    challenge_id: str = Field(pattern=_CHAT_REF_PATTERN)


class ChatMaskedDevice(BaseModel):
    """Bezpieczna projekcja aktywnego urządzenia po weryfikacji SMS."""

    model_config = ConfigDict(extra="forbid")

    device_ref: str = Field(pattern=_CHAT_REF_PATTERN)
    producer: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=200)
    serial: str = Field(min_length=4, max_length=300)
    serial_last4: str = Field(min_length=4, max_length=4)
    image_url: str | None = Field(default=None, max_length=2000)
    location: str | None = Field(default=None, max_length=300)
    active: bool


class ChatMaskedDevicesResponse(BaseModel):
    """Lista aktywnych urządzeń dostępna po potwierdzeniu SMS."""

    model_config = ConfigDict(extra="forbid")

    customer_ref: str = Field(pattern=_CHAT_REF_PATTERN)
    devices: list[ChatMaskedDevice]


class ChatCaseCreateRequest(BaseModel):
    """Sprawa przekazywana asynchronicznie z CHAT_KP."""

    model_config = ConfigDict(extra="forbid")

    source_channel: Literal["chat"]
    source_system: Literal["chat_kp"]
    conversation_ref: str = Field(pattern=_CHAT_REF_PATTERN)
    category: ChatCaseCategory
    summary: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=5000)
    customer_ref: str | None = Field(default=None, pattern=_CHAT_REF_PATTERN)
    customer_match_status: ChatCustomerMatchStatus
    customer_nip: str | None = None
    customer_name: str | None = Field(default=None, min_length=2, max_length=200)
    device_ref: str | None = Field(default=None, pattern=_CHAT_REF_PATTERN)
    device_refs: list[str] | None = None
    sms_challenge_id: str | None = Field(default=None, pattern=_CHAT_REF_PATTERN)
    contact_phone: str | None = Field(default=None, min_length=8, max_length=24)
    phone_verification_status: ChatSmsVerificationStatus | None = None
    customer_confirmed: Literal[True]
    privacy_notice_accepted: Literal[True]
    privacy_notice_version: str = Field(min_length=1, max_length=32)
    privacy_notice_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def normalize_device_selection(self) -> ChatCaseCreateRequest:
        normalized: list[str] = []
        for value in self.device_refs or []:
            candidate = str(value).strip()
            if not re.fullmatch(_CHAT_REF_PATTERN, candidate):
                raise ValueError("Nieprawidłowa referencja urządzenia.")
            if candidate not in normalized:
                normalized.append(candidate)
        if len(normalized) > 20:
            raise ValueError("Jedna sprawa może dotyczyć maksymalnie 20 urządzeń.")
        if self.device_ref and normalized and normalized != [self.device_ref]:
            raise ValueError(
                "Gdy podano device_ref i device_refs, lista musi zawierać wyłącznie device_ref."
            )
        self.device_refs = normalized or None
        return self

    def selected_device_refs(self) -> list[str]:
        """Zwraca znormalizowany wybór urządzeń z zachowaniem starego pola."""
        if self.device_refs:
            return list(self.device_refs)
        return [self.device_ref] if self.device_ref else []


class ChatCaseResponse(BaseModel):
    """Identyfikator i stan sprawy zapisanej w CTIP."""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(pattern=_CHAT_REF_PATTERN)
    external_reference: str = Field(pattern=_CHAT_REF_PATTERN)
    status: Literal[
        "queued",
        "accepted",
        "in_progress",
        "resolved",
        "rejected",
        "failed",
    ]
    category: ChatCaseCategory
    created_at: datetime
    updated_at: datetime
