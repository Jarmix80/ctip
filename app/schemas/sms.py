"""Schematy obsługi wiadomości SMS."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class SmsSendRequest(BaseModel):
    """Żądanie wysłania SMS z poziomu UI."""

    dest: str = Field(..., description="Docelowy numer MSISDN")
    text: str | None = Field(None, min_length=1, max_length=480)
    call_id: int | None = Field(None, description="Powiązane połączenie CTIP")
    template_id: int | None = Field(None, description="Szablon użyty do wysyłki")
    origin: str = Field(default="ui")

    model_config = ConfigDict(extra="forbid")

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
