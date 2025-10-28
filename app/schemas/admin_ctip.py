"""Schematy konfiguracji CTIP używane w panelu administratora."""

from __future__ import annotations

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
