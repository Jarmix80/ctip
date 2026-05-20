"""Schematy Pydantic dla modułu CTIP AI Asystent."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

AssistantWorkerKey = Literal[
    "ksero_partner_analyst",
    "opiekun_klienta",
    "diagnosta_bazy_ms",
]


class AssistantWorkerInfo(BaseModel):
    """Definicja dostępnego profilu pracownika AI."""

    key: AssistantWorkerKey
    name: str
    description: str


class AssistantChatCreateRequest(BaseModel):
    """Payload tworzenia nowego czatu."""

    title: str | None = Field(default=None, max_length=200)
    worker_key: AssistantWorkerKey | None = None

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class AssistantChatSummary(BaseModel):
    """Skrót informacji o wątku rozmowy."""

    id: int
    title: str
    worker_key: AssistantWorkerKey
    status: Literal["active", "archived", "deleted"]
    created_at: datetime
    updated_at: datetime
    last_activity_at: datetime


class AssistantChatMessageRead(BaseModel):
    """Wiadomość w historii czatu."""

    id: int
    thread_id: int
    role: Literal["user", "assistant", "system", "tool"]
    content: str
    created_at: datetime
    openai_response_id: str | None = None
    model_name: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


class AssistantChatDetail(BaseModel):
    """Pełny widok wątku rozmowy."""

    thread: AssistantChatSummary
    messages: list[AssistantChatMessageRead]


class AssistantChatPromptRequest(BaseModel):
    """Wiadomość użytkownika wysyłana do asystenta."""

    prompt: str = Field(min_length=1, max_length=10_000)
    stream: bool = True

    @field_validator("prompt")
    @classmethod
    def strip_prompt(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Prompt nie może być pusty.")
        return stripped


class AssistantSourceInfo(BaseModel):
    """Źródło danych użyte przy odpowiedzi."""

    tool: Literal[
        "firebird_read",
        "firebird_business_read",
        "firebird_knowledge_read",
        "sheets_read",
        "imap_read",
        "ctip_schema_read",
        "email_send_report",
        "workflow_devices_audit",
    ]
    row_count: int | None = None
    duration_ms: int | None = None


class AssistantPendingActionInfo(BaseModel):
    """Akcja oczekująca na świadome wykonanie przez użytkownika."""

    id: int
    type: Literal["workflow_devices_chat_sheet_stage"]
    label: str
    description: str | None = None
    summary: dict[str, Any] | None = None


class AssistantChatMessageResponse(BaseModel):
    """Odpowiedź asystenta dla pojedynczego promptu."""

    user_message_id: int
    assistant_message: AssistantChatMessageRead
    sources: list[AssistantSourceInfo] = Field(default_factory=list)
    blocked_as_change_request: bool = False
    change_request_id: int | None = None
    pending_action: AssistantPendingActionInfo | None = None


class AssistantChangeRequestCreate(BaseModel):
    """Ręczne utworzenie wniosku o zmianę z poziomu UI/API."""

    request_text: str = Field(min_length=1, max_length=4000)
    justification: str | None = Field(default=None, max_length=4000)
    thread_id: int | None = Field(default=None, ge=1)
    message_id: int | None = Field(default=None, ge=1)
    payload: dict[str, Any] | None = None

    @field_validator("request_text", "justification")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class AssistantChangeRequestDecision(BaseModel):
    """Decyzja dla wniosku o zmianę."""

    note: str | None = Field(default=None, max_length=4000)

    @field_validator("note")
    @classmethod
    def strip_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class AssistantChangeRequestRead(BaseModel):
    """Widok wniosku o zmianę."""

    id: int
    created_by: int | None = None
    thread_id: int | None = None
    message_id: int | None = None
    request_text: str
    justification: str | None = None
    payload: dict[str, Any] | None = None
    status: Literal["pending", "approved", "rejected", "executed"]
    decided_by: int | None = None
    decided_at: datetime | None = None
    decision_note: str | None = None
    created_at: datetime
    updated_at: datetime


class AssistantActionExecutionResponse(BaseModel):
    """Wynik wykonania kontrolowanej akcji asystenta."""

    change_request: AssistantChangeRequestRead
    result: dict[str, Any]


class AssistantWeeklyInsightRead(BaseModel):
    """Widok wpisu tygodniowego raportu usprawnień."""

    id: int
    week_start: date
    generated_at: datetime
    generated_by: int | None = None
    summary: str
    details: dict[str, Any] | None = None


class AssistantLearningProfileRead(BaseModel):
    """Widok profilu uczenia asystenta dla użytkownika."""

    user_id: int
    personalization_enabled: bool
    preferences: dict[str, Any] | None = None
    memory_notes: str | None = None
    updated_at: datetime


class AssistantLearningProfileUpdate(BaseModel):
    """Aktualizacja profilu uczenia asystenta (admin-only)."""

    personalization_enabled: bool | None = None
    preferences: dict[str, Any] | None = None
    memory_notes: str | None = Field(default=None, max_length=10000)

    @field_validator("memory_notes")
    @classmethod
    def strip_memory_notes(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None
