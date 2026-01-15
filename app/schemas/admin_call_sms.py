"""Schematy panelu administratora dla SMS polaczen."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CallSmsConfigResponse(BaseModel):
    """Aktualna konfiguracja automatycznych SMS dla dzwoniacych."""

    enabled: bool
    inbound_enabled: bool
    outbound_enabled: bool
    inbound_answered_enabled: bool
    inbound_answered_text: str
    inbound_missed_enabled: bool
    inbound_missed_text: str
    inbound_repeat_answered_enabled: bool
    inbound_repeat_answered_text: str
    inbound_repeat_missed_enabled: bool
    inbound_repeat_missed_text: str
    outbound_answered_enabled: bool
    outbound_answered_text: str
    outbound_missed_enabled: bool
    outbound_missed_text: str
    outbound_repeat_answered_enabled: bool
    outbound_repeat_answered_text: str
    outbound_repeat_missed_enabled: bool
    outbound_repeat_missed_text: str
    cooldown_mode: Literal["never", "after_days", "always"]
    cooldown_days: int = Field(ge=1, le=3650)
    opt_out_numbers: str


class CallSmsConfigUpdate(CallSmsConfigResponse):
    """Zadanie aktualizacji konfiguracji SMS dla dzwoniacych."""


class CallSmsBulkRequest(BaseModel):
    """Parametry masowej wysylki SMS do numerow z historii polaczen."""

    text: str = Field(..., min_length=1, max_length=480)
    days_back: int | None = Field(default=None, ge=1, le=3650)
    direction: Literal["IN", "OUT", "ALL"] = "IN"


class CallSmsBulkResponse(BaseModel):
    """Podsumowanie masowej wysylki SMS."""

    created: int
    skipped_invalid: int
    skipped_non_mobile: int
    skipped_opt_out: int
    skipped_cooldown: int
    skipped_duplicates: int
    total_unique: int
    bulk_id: str | None = None


class CallSmsHistoryEntry(BaseModel):
    """Wpis historii wysylek SMS dla polaczen."""

    id: int
    created_at: datetime
    dest: str
    status: str
    text: str
    call_id: int | None
    internal_ext: str | None = None
    direction: str | None = None
    disposition: str | None = None
    scenario: str | None = None
    repeat: bool | None = None
    provider_status: str | None = None
    provider_message_id: str | None = None
    error_msg: str | None = None
    bulk_id: str | None = None


class CallSmsHistoryResponse(BaseModel):
    """Odpowiedz z historia SMS dla polaczen."""

    generated_at: datetime
    limit: int
    status: str | None = None
    scenario: str | None = None
    items: list[CallSmsHistoryEntry]
