"""Schematy odpowiedzi dla połączeń CTIP."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .contact import ContactSchema
from .sms import SmsHistoryItem


class CallFilters(BaseModel):
    """Parametry filtrowania listy połączeń."""

    direction: str | None = Field(None, description="IN/OUT")
    status: str | None = Field(None, description="ANSWERED/NO_ANSWER/FAILED/BUSY/UNKNOWN")
    ext: str | None = Field(None, description="Numer wewnętrzny")
    search: str | None = Field(None, description="Numer lub nazwa klienta")
    date_from: datetime | None = None
    date_to: datetime | None = None
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class CallListItem(BaseModel):
    """Podstawowe dane o połączeniu używane na liście."""

    id: int
    direction: str
    ext: str
    ext_name: str | None
    number: str | None
    started_at: datetime
    duration_s: int | None
    disposition: str
    latest_sms: SmsHistoryItem | None

    contact: ContactSchema | None


class CallEventSchema(BaseModel):
    """Pojedyncze zdarzenie CTIP."""

    ts: datetime
    typ: str
    payload: str | None
    ext: str | None
    number: str | None


class CallDetail(CallListItem):
    """Szczegółowy widok połączenia."""

    connected_at: datetime | None
    ended_at: datetime | None
    notes: str | None
    events: list[CallEventSchema]
    sms_history: list[SmsHistoryItem]
