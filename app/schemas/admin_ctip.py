"""Schematy konfiguracji CTIP używane w panelu administratora."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class AdminIvrMapEntry(BaseModel):
    """Pojedynczy wpis mapowania IVR -> SMS."""

    ext: str = Field(min_length=1, max_length=32)
    digit: int = Field(ge=0, le=99)
    sms_text: str = Field(min_length=1, max_length=4096)
    enabled: bool


class AdminIvrMapListResponse(BaseModel):
    """Lista wpisów mapowania IVR."""

    items: list[AdminIvrMapEntry]


class AdminIvrMapUpdate(BaseModel):
    """Aktualizacja wpisu mapowania IVR."""

    digit: int = Field(ge=0, le=99)
    sms_text: str = Field(min_length=1, max_length=4096)
    enabled: bool = True


class AdminIvrMapCreate(AdminIvrMapUpdate):
    """Tworzenie nowego wpisu mapowania."""

    ext: str = Field(min_length=1, max_length=32)


class AdminIvrSmsHistoryEntry(BaseModel):
    """Rekord historii wysyłek z automatyzacji IVR."""

    id: int
    created_at: datetime
    dest: str
    status: str
    text: str
    call_id: int | None = None
    internal_ext: str | None = None
    digit: int | None = None
    provider_status: str | None = None
    provider_message_id: str | None = None
    error_msg: str | None = None


class AdminIvrSmsHistoryResponse(BaseModel):
    """Odpowiedź z historią wysyłek IVR."""

    generated_at: datetime
    limit: int
    status: str | None = None
    ext: str | None = None
    items: list[AdminIvrSmsHistoryEntry]
