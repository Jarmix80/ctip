"""Kontrakty wspólnego katalogu tożsamości botów."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

IdentityChannel = Literal["voice", "chat"]


class IdentityResolveRequest(BaseModel):
    channel: IdentityChannel
    conversation_ref: str = Field(min_length=1, max_length=128)
    phone: str = Field(min_length=7, max_length=32)


class IdentityResolveResponse(BaseModel):
    status: Literal["exact", "ambiguous", "not_found", "disputed", "stale"]
    resolution_ref: str
    candidate_count: int = Field(ge=0)
    identity_ref: str | None = None
    customer_ref: str | None = None
    display_name: str | None = None
    company_name: str | None = None
    binding_source: str | None = None
    authorization_state: str | None = None
    requires_current_confirmation: bool = True
    requires_nip_verification: bool = False
    expires_at: datetime


class IdentityConfirmRequest(BaseModel):
    confirmed: bool
    conversation_ref: str = Field(min_length=1, max_length=128)


class IdentityConfirmResponse(BaseModel):
    confirmed: bool
    status: Literal["confirmed", "rejected", "expired", "not_exact", "nip_required"]
    disclosure_grant: str | None = None
    disclosure_level: Literal["full_serial", "masked"] | None = None
    expires_at: datetime | None = None


class IdentityNipVerifyRequest(BaseModel):
    conversation_ref: str = Field(min_length=1, max_length=128)
    nip: str = Field(min_length=10, max_length=20)


class IdentityNipVerifyResponse(BaseModel):
    verified: bool
    status: Literal["verified", "invalid", "expired", "not_required", "blocked"]
    attempts_remaining: int = Field(ge=0, le=3)


class IdentitySmsChallengeRequest(BaseModel):
    channel: IdentityChannel
    conversation_ref: str = Field(min_length=1, max_length=128)
    phone: str = Field(min_length=7, max_length=32)


class IdentitySmsChallengeResponse(BaseModel):
    challenge_ref: str
    expires_at: datetime
    test_code: str | None = None


class IdentitySmsVerifyRequest(BaseModel):
    channel: IdentityChannel
    conversation_ref: str = Field(min_length=1, max_length=128)
    code: str = Field(pattern=r"^\d{6}$")


class IdentitySmsVerifyResponse(BaseModel):
    verified: bool
    status: Literal["verified", "invalid", "expired", "blocked"]
    attempts_remaining: int = Field(ge=0, le=3)


class IdentityDevicesRequest(BaseModel):
    disclosure_grant: str = Field(min_length=20, max_length=256)
    channel: IdentityChannel
    conversation_ref: str = Field(min_length=1, max_length=128)


class IdentityDevice(BaseModel):
    device_ref: str
    producer: str | None = None
    model: str | None = None
    serial: str | None = None
    serial_last4: str | None = None
    image_url: str | None = None
    location: str | None = None
    active: bool


class IdentityDevicesResponse(BaseModel):
    customer_ref: str
    disclosure_level: Literal["full_serial", "masked"]
    devices: list[IdentityDevice]


class IdentityOverrideRequest(BaseModel):
    phone_ref: str = Field(pattern=r"^[0-9a-f]{64}$")
    subject_id: int = Field(ge=1)
    binding_id: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=500)


class IdentityCandidate(BaseModel):
    subject_id: int
    binding_id: int
    identity_ref: str
    customer_ref: str
    display_name: str | None
    company_name: str
    source: str
    authorization_state: str
    active: bool


class IdentityDuplicateGroup(BaseModel):
    phone_ref: str
    phone_last4: str
    candidate_count: int
    has_override: bool
    candidates: list[IdentityCandidate]


class IdentityDuplicateListResponse(BaseModel):
    items: list[IdentityDuplicateGroup]


class IdentitySyncStatusResponse(BaseModel):
    configured: bool
    status: str
    last_completed_at: datetime | None
    age_seconds: int | None
    accounts_seen: int
    customers_seen: int
    devices_seen: int
    duplicate_phones: int
    source_revision: str
    error_message: str | None


class PromoteSmsBindingRequest(BaseModel):
    phone: str = Field(min_length=7, max_length=32)
    customer_ref: str = Field(min_length=1, max_length=128)
    company_name: str = Field(min_length=1, max_length=300)
    case_ref: str = Field(min_length=1, max_length=128)
    display_name: str | None = Field(default=None, max_length=200)

    @field_validator("display_name", mode="before")
    @classmethod
    def empty_name_is_none(cls, value: str | None) -> str | None:
        return value.strip() or None if isinstance(value, str) else value
