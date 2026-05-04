"""Orkiestrator modelu LLM dla CTIP AI Asystenta."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import AssistantChatMessage
from app.services.assistant_learning import (
    build_learning_prompt_context,
    infer_business_intent_from_prompt,
    load_user_assistant_preferences,
    render_business_tool_answer,
)
from app.services.assistant_tools import AssistantDataTools, AssistantToolResult
from app.services.assistant_workers import get_worker_profile
from app.services.settings_store import build_store

_SETTINGS_NAMESPACE = "assistant"
_WRITE_INTENT_RE = re.compile(
    r"\b("
    r"zapisz|zmien|zmień|usun|usuń|dodaj|utworz|utwórz|wystaw|anuluj|akceptuj|zatwierdz|"
    r"insert|update|delete|merge|create|drop|alter|grant|revoke"
    r")\b",
    re.IGNORECASE,
)


@dataclass(slots=True, frozen=True)
class AssistantRuntimeConfig:
    """Runtime konfiguracja działania modułu asystenta."""

    enabled: bool
    api_key: str
    model_name: str
    max_tool_iterations: int
    max_output_tokens: int
    daily_message_limit: int
    timeout_seconds: int
    firebird_row_limit: int
    sheets_row_limit: int
    imap_row_limit: int
    schema_row_limit: int
    redact_patterns: list[str]


@dataclass(slots=True, frozen=True)
class AssistantGenerationResult:
    """Wynik pełnej odpowiedzi asystenta dla pojedynczego promptu."""

    answer_text: str
    response_id: str | None
    model_name: str | None
    input_tokens: int | None
    output_tokens: int | None
    tool_results: list[AssistantToolResult]
    sources: list[dict[str, Any]]
    blocked_as_change_request: bool


def _to_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "t", "yes", "on"}


def _to_int(value: Any, default: int, *, min_value: int, max_value: int) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return default
    if parsed < min_value:
        return min_value
    if parsed > max_value:
        return max_value
    return parsed


def _sanitize_text(text: str, redact_patterns: list[str]) -> str:
    sanitized = text
    default_patterns = [
        r"(?i)\b(haslo|hasło|password)\b\s*[:=]\s*\S+",
        r"(?i)\b(token|api[_-]?key)\b\s*[:=]\s*\S+",
        r"(?i)\b(secret)\b\s*[:=]\s*\S+",
    ]
    all_patterns = [*default_patterns, *redact_patterns]
    for pattern in all_patterns:
        try:
            sanitized = re.sub(pattern, "[REDACTED]", sanitized)
        except re.error:
            continue
    return sanitized


def detect_change_intent(prompt: str) -> bool:
    """Wykrywa, czy prompt prawdopodobnie dotyczy modyfikacji danych."""
    return bool(_WRITE_INTENT_RE.search(prompt or ""))


async def load_assistant_runtime_config(
    session: AsyncSession,
    *,
    secret_key: str | None,
) -> AssistantRuntimeConfig:
    """Ładuje konfigurację asystenta z `admin_setting` oraz fallback z env."""
    store = build_store(secret_key)
    stored = await store.get_namespace(session, _SETTINGS_NAMESPACE)
    redact_patterns_raw = (stored.get("redact_patterns") or "").strip()
    redact_patterns = [item.strip() for item in redact_patterns_raw.splitlines() if item.strip()]

    api_key = (
        (stored.get("openai_api_key") or "").strip()
        or (stored.get("openai_api_chat_kp") or "").strip()
        or (stored.get("api_key") or "").strip()
        or (stored.get("OPENAI_API_CHAT_KP") or "").strip()
        or (stored.get("OPENAI_API_KEY") or "").strip()
        or os.getenv("OPENAI_API_CHAT_KP", "").strip()
        or os.getenv("OPENAI_API_KEY", "").strip()
        or str(settings.openai_api_chat_kp or "").strip()
        or str(settings.openai_api_key or "").strip()
    )
    return AssistantRuntimeConfig(
        enabled=_to_bool(stored.get("enabled"), True),
        api_key=api_key,
        model_name=(stored.get("model_name") or "gpt-4.1-mini").strip() or "gpt-4.1-mini",
        max_tool_iterations=_to_int(
            stored.get("max_tool_iterations"), 4, min_value=1, max_value=12
        ),
        max_output_tokens=_to_int(
            stored.get("max_output_tokens"), 1200, min_value=200, max_value=4000
        ),
        daily_message_limit=_to_int(
            stored.get("daily_message_limit"), 120, min_value=10, max_value=2000
        ),
        timeout_seconds=_to_int(stored.get("timeout_seconds"), 40, min_value=10, max_value=180),
        firebird_row_limit=_to_int(
            stored.get("firebird_row_limit"), 200, min_value=20, max_value=2000
        ),
        sheets_row_limit=_to_int(stored.get("sheets_row_limit"), 200, min_value=20, max_value=2000),
        imap_row_limit=_to_int(stored.get("imap_row_limit"), 30, min_value=5, max_value=200),
        schema_row_limit=_to_int(stored.get("schema_row_limit"), 50, min_value=5, max_value=300),
        redact_patterns=redact_patterns,
    )


async def count_user_messages_today(session: AsyncSession, *, user_id: int) -> int:
    """Liczy liczbę wiadomości użytkownika zapisanych dziś (UTC)."""
    today_count_stmt = select(func.count(AssistantChatMessage.id)).where(
        AssistantChatMessage.user_id == user_id,
        AssistantChatMessage.role == "user",
        func.date(AssistantChatMessage.created_at) == func.current_date(),
    )
    return int((await session.execute(today_count_stmt)).scalar() or 0)


def _extract_text_from_response(payload: dict[str, Any]) -> str:
    output = payload.get("output")
    chunks: list[str] = []
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "message":
                content = item.get("content")
                if isinstance(content, list):
                    for part in content:
                        if not isinstance(part, dict):
                            continue
                        if part.get("type") in {"output_text", "text"}:
                            text = part.get("text")
                            if isinstance(text, str) and text.strip():
                                chunks.append(text.strip())
                elif isinstance(content, str) and content.strip():
                    chunks.append(content.strip())
            elif item.get("type") in {"output_text", "text"}:
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    chunks.append(text.strip())
    if chunks:
        return "\n\n".join(chunks).strip()
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()
    return ""


def _extract_tool_calls(payload: dict[str, Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    output = payload.get("output")
    if not isinstance(output, list):
        return calls
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") not in {"function_call", "tool_call"}:
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        raw_args = item.get("arguments")
        parsed_args: dict[str, Any]
        if isinstance(raw_args, dict):
            parsed_args = raw_args
        elif isinstance(raw_args, str):
            try:
                loaded = json.loads(raw_args)
                parsed_args = loaded if isinstance(loaded, dict) else {}
            except json.JSONDecodeError:
                parsed_args = {}
        else:
            parsed_args = {}
        call_id = str(item.get("call_id") or item.get("id") or "").strip()
        calls.append({"name": name, "arguments": parsed_args, "call_id": call_id})
    return calls


class AssistantRuntime:
    """Silnik generowania odpowiedzi asystenta z function-calling."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        secret_key: str | None,
    ) -> None:
        self._session = session
        self._secret_key = secret_key

    async def generate(
        self,
        *,
        user_id: int,
        prompt: str,
        history: list[dict[str, str]],
        worker_key: str | None = None,
    ) -> AssistantGenerationResult:
        config = await load_assistant_runtime_config(self._session, secret_key=self._secret_key)
        clean_prompt = _sanitize_text(prompt, config.redact_patterns)
        if not config.enabled:
            return AssistantGenerationResult(
                answer_text="Moduł asystenta jest wyłączony przez konfigurację administratora.",
                response_id=None,
                model_name=None,
                input_tokens=None,
                output_tokens=None,
                tool_results=[],
                sources=[],
                blocked_as_change_request=False,
            )

        if detect_change_intent(clean_prompt):
            return AssistantGenerationResult(
                answer_text=(
                    "Tryb asystenta jest tylko do odczytu. Nie wykonuję modyfikacji danych. "
                    "Utwórz wniosek o zmianę i przekaż go do akceptacji."
                ),
                response_id=None,
                model_name=None,
                input_tokens=None,
                output_tokens=None,
                tool_results=[],
                sources=[],
                blocked_as_change_request=True,
            )

        used_today = await count_user_messages_today(self._session, user_id=user_id)
        if used_today >= config.daily_message_limit:
            return AssistantGenerationResult(
                answer_text=(
                    "Przekroczono dzienny limit wiadomości dla asystenta. "
                    "Spróbuj ponownie po północy UTC lub skontaktuj się z administratorem."
                ),
                response_id=None,
                model_name=None,
                input_tokens=None,
                output_tokens=None,
                tool_results=[],
                sources=[],
                blocked_as_change_request=False,
            )

        tools = AssistantDataTools(
            self._session,
            settings_store_secret=self._secret_key,
            user_id=user_id,
        )
        worker_profile = get_worker_profile(worker_key)
        user_preferences = await load_user_assistant_preferences(self._session, user_id=user_id)
        inferred_business_intent = infer_business_intent_from_prompt(
            clean_prompt, preferences=user_preferences
        )
        if inferred_business_intent:
            shortcut_result = await tools.execute_tool(
                "firebird_business_read",
                inferred_business_intent,
            )
            if shortcut_result.status == "success":
                return AssistantGenerationResult(
                    answer_text=render_business_tool_answer(shortcut_result.payload),
                    response_id=None,
                    model_name="rule-based-business-router",
                    input_tokens=None,
                    output_tokens=None,
                    tool_results=[shortcut_result],
                    sources=[
                        {
                            "tool": shortcut_result.tool_name,
                            "row_count": shortcut_result.row_count,
                            "duration_ms": shortcut_result.duration_ms,
                        }
                    ],
                    blocked_as_change_request=False,
                )

        if not config.api_key:
            return AssistantGenerationResult(
                answer_text=(
                    "Brak konfiguracji klucza OpenAI (`assistant.openai_api_key` lub `OPENAI_API_CHAT_KP`). "
                    "Skonfiguruj sekret w panelu administratora."
                ),
                response_id=None,
                model_name=config.model_name,
                input_tokens=None,
                output_tokens=None,
                tool_results=[],
                sources=[],
                blocked_as_change_request=False,
            )

        tool_results: list[AssistantToolResult] = []
        response_payload = await self._call_responses_api(
            api_key=config.api_key,
            model_name=config.model_name,
            timeout_seconds=config.timeout_seconds,
            payload={
                "model": config.model_name,
                "max_output_tokens": config.max_output_tokens,
                "input": self._build_input_messages(
                    clean_prompt,
                    history,
                    learning_context=build_learning_prompt_context(user_preferences),
                    worker_prompt=worker_profile.prompt_addendum,
                ),
                "tools": self._tool_definitions(
                    firebird_limit=config.firebird_row_limit,
                    sheets_limit=config.sheets_row_limit,
                    imap_limit=config.imap_row_limit,
                    schema_limit=config.schema_row_limit,
                ),
            },
        )

        response_id = (
            str(response_payload.get("id")).strip() if response_payload.get("id") else None
        )
        answer_text = _extract_text_from_response(response_payload)
        tool_calls = _extract_tool_calls(response_payload)
        iterations = 0

        while tool_calls and iterations < config.max_tool_iterations:
            iterations += 1
            function_call_outputs: list[dict[str, Any]] = []
            for call in tool_calls:
                result = await tools.execute_tool(call["name"], call.get("arguments") or {})
                tool_results.append(result)
                output_payload = (
                    result.payload
                    if result.status == "success"
                    else {
                        "error": result.error_message or "Narzędzie zakończyło się błędem.",
                        "status": result.status,
                    }
                )
                function_call_outputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.get("call_id") or call["name"],
                        "output": json.dumps(output_payload, ensure_ascii=False),
                    }
                )

            response_payload = await self._call_responses_api(
                api_key=config.api_key,
                model_name=config.model_name,
                timeout_seconds=config.timeout_seconds,
                payload={
                    "model": config.model_name,
                    "previous_response_id": response_id,
                    "input": function_call_outputs,
                    "max_output_tokens": config.max_output_tokens,
                },
            )
            response_id = (
                str(response_payload.get("id")).strip()
                if response_payload.get("id")
                else response_id
            )
            latest_text = _extract_text_from_response(response_payload)
            if latest_text:
                answer_text = latest_text
            tool_calls = _extract_tool_calls(response_payload)

        if not answer_text:
            answer_text = "Nie udało się wygenerować odpowiedzi. Spróbuj ponownie za chwilę."

        usage = response_payload.get("usage") if isinstance(response_payload, dict) else {}
        input_tokens = (
            int(usage.get("input_tokens")) if usage and usage.get("input_tokens") else None
        )
        output_tokens = (
            int(usage.get("output_tokens")) if usage and usage.get("output_tokens") else None
        )

        sources: list[dict[str, Any]] = []
        for result in tool_results:
            if result.status != "success":
                continue
            sources.append(
                {
                    "tool": result.tool_name,
                    "row_count": result.row_count,
                    "duration_ms": result.duration_ms,
                }
            )

        return AssistantGenerationResult(
            answer_text=_sanitize_text(answer_text, config.redact_patterns),
            response_id=response_id,
            model_name=config.model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tool_results=tool_results,
            sources=sources,
            blocked_as_change_request=False,
        )

    def _build_input_messages(
        self,
        prompt: str,
        history: list[dict[str, str]],
        *,
        learning_context: str = "",
        worker_prompt: str = "",
    ) -> list[dict[str, str]]:
        system_prompt = (
            "Jesteś CTIP AI Asystentem w trybie tylko odczytu. "
            "Możesz korzystać wyłącznie z narzędzi firebird_read, firebird_business_read, "
            "firebird_knowledge_read, sheets_read, imap_read, ctip_schema_read i email_send_report. "
            "Gdy pytanie dotyczy schematu PostgreSQL CTIP (tabele `ctip.*`, relacje workflow) "
            "użyj ctip_schema_read. "
            "Do pytań o schemat bazy PostgreSQL CTIP nie używaj firebird_read ani sheets_read. "
            "Dla wiedzy o strukturze i działaniu bazy Firebird Menadżera Serwisu "
            "(np. tabele MASZYNA/KLIENT/CPC/MODEL, trigger, generator, kolumny MS) "
            "zawsze najpierw użyj firebird_knowledge_read (lokalny indeks wiedzy repozytorium). "
            "Dla pytań biznesowych po naturalnym opisie najpierw użyj firebird_business_read: "
            "`devices_by_company` dla listy urządzeń firmy, "
            "`monthly_average_print_by_model` dla średnich miesięcznych liczników modelu, "
            "`company_monthly_print_summary` dla miesięcznego podsumowania wydruków firmy, "
            "`top_models_by_volume` dla rankingu modeli po wolumenie wydruków, "
            "`device_monthly_print_by_serial` dla historii miesięcznej po numerze seryjnym, "
            "`active_devices_on_contracts` dla listy aktywnych urządzeń na umowach, "
            "`active_devices_on_contracts_count` dla dokładnej liczby aktywnych urządzeń na umowach, "
            "`contract_settlement_period_explainer` dla wyjaśnienia logiki okresów rozliczeń umów. "
            "Gdy użytkownik prosi o wysyłkę raportu e-mail, użyj email_send_report "
            "(odbiorca + temat + format, raport z ostatniego wyniku narzędzia danych). "
            "Jeśli użytkownik nie poda okresu dla modelu, przyjmij ostatnie 12 miesięcy "
            "(ustaw `months_back=12`). "
            "Jeśli model podany przez użytkownika ma różny zapis (np. MPC3004 vs MP C3004), "
            "traktuj to jako ten sam model i użyj firebird_business_read. "
            "Nigdy nie wykonuj operacji zapisu ani DDL/DML. "
            "Jeśli użytkownik prosi o zmianę danych, odmów i zasugeruj utworzenie wniosku o zmianę."
        )
        if learning_context.strip():
            system_prompt = (
                f"{system_prompt}\n\nKontekst uczenia użytkownika:\n{learning_context.strip()}"
            )
        if worker_prompt.strip():
            system_prompt = f"{system_prompt}\n\nProfil pracownika AI:\n{worker_prompt.strip()}"
        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        for item in history[-20:]:
            role = (item.get("role") or "").strip().lower()
            content = (item.get("content") or "").strip()
            if role in {"user", "assistant"} and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": prompt})
        return messages

    def _tool_definitions(
        self,
        *,
        firebird_limit: int,
        sheets_limit: int,
        imap_limit: int,
        schema_limit: int,
    ) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "name": "firebird_business_read",
                "description": (
                    "Uruchamia gotowe zapytania biznesowe Firebird dla pytań w naturalnym języku. "
                    "Używaj w pierwszej kolejności dla: listy urządzeń firmy oraz średnich miesięcznych "
                    "wydruków dla modelu (domyślnie ostatnie 12 miesięcy, gdy brak wskazanego okresu). "
                    f"Maksymalny row_limit: {firebird_limit}."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "intent": {
                            "type": "string",
                            "enum": [
                                "devices_by_company",
                                "monthly_average_print_by_model",
                                "company_monthly_print_summary",
                                "top_models_by_volume",
                                "device_monthly_print_by_serial",
                                "active_devices_on_contracts",
                                "active_devices_on_contracts_count",
                                "contract_settlement_period_explainer",
                            ],
                        },
                        "company_name": {"type": "string"},
                        "model_name": {"type": "string"},
                        "serial_number": {"type": "string"},
                        "months_back": {"type": "integer", "minimum": 1, "maximum": 120},
                        "row_limit": {"type": "integer", "minimum": 1, "maximum": firebird_limit},
                    },
                    "required": ["intent"],
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": "email_send_report",
                "description": (
                    "Wysyła raport jako załącznik e-mail przez systemową skrzynkę SMTP CTIP "
                    "(tę samą, której używają powiadomienia). Raport budowany jest z ostatniego "
                    "udanego wyniku narzędzia danych."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "recipient_email": {"type": "string"},
                        "subject": {"type": "string"},
                        "message_body": {"type": "string"},
                        "report_format": {"type": "string", "enum": ["csv", "json", "txt"]},
                        "source_tool": {
                            "type": "string",
                            "enum": [
                                "firebird_read",
                                "firebird_business_read",
                                "firebird_knowledge_read",
                                "sheets_read",
                                "imap_read",
                                "ctip_schema_read",
                            ],
                        },
                        "report_title": {"type": "string"},
                    },
                    "required": ["recipient_email"],
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": "firebird_knowledge_read",
                "description": (
                    "Czyta lokalną bazę wiedzy Firebird MS (repozytorium bazams zindeksowane do JSON). "
                    "Używaj do pytań o tabele, kolumny, nazwy pól, kontekst triggerów i analizę struktury "
                    "bez odpytywania bazy danych. "
                    f"Maksymalny row_limit: {schema_limit}."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "table_name": {"type": "string"},
                        "topic": {"type": "string"},
                        "include_columns": {"type": "boolean"},
                        "row_limit": {"type": "integer", "minimum": 1, "maximum": schema_limit},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": "firebird_read",
                "description": (
                    "Wykonuje wyłącznie zapytania SELECT/CTE do Firebird. "
                    f"Maksymalny row_limit: {firebird_limit}."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "sql": {"type": "string"},
                        "row_limit": {"type": "integer", "minimum": 1, "maximum": firebird_limit},
                    },
                    "required": ["sql"],
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": "sheets_read",
                "description": (
                    "Odczytuje dane z Google Sheets (scope read-only). "
                    f"Maksymalny row_limit: {sheets_limit}."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "worksheet": {"type": "string"},
                        "range_name": {"type": "string"},
                        "row_limit": {"type": "integer", "minimum": 1, "maximum": sheets_limit},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": "imap_read",
                "description": (
                    "Odczytuje nagłówki wiadomości z IMAP użytkownika (tylko odczyt). "
                    f"Maksymalny row_limit: {imap_limit}."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "folder": {"type": "string"},
                        "unread_only": {"type": "boolean"},
                        "since_days": {"type": "integer", "minimum": 0, "maximum": 3650},
                        "row_limit": {"type": "integer", "minimum": 1, "maximum": imap_limit},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": "ctip_schema_read",
                "description": (
                    "Odczytuje metadane PostgreSQL schematu `ctip`: tabele, kolumny i relacje FK. "
                    f"Maksymalny row_limit: {schema_limit}."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "table_name": {"type": "string"},
                        "include_columns": {"type": "boolean"},
                        "include_relationships": {"type": "boolean"},
                        "row_limit": {"type": "integer", "minimum": 1, "maximum": schema_limit},
                    },
                    "additionalProperties": False,
                },
            },
        ]

    async def _call_responses_api(
        self,
        *,
        api_key: str,
        model_name: str,
        timeout_seconds: int,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        timeout = httpx.Timeout(timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                "https://api.openai.com/v1/responses",
                headers=headers,
                json=payload,
            )
            if response.status_code >= 400:
                detail = response.text.strip()
                raise RuntimeError(
                    f"OpenAI Responses API zwróciło błąd {response.status_code}: {detail}"
                )
            data = response.json()
            if not isinstance(data, dict):
                raise RuntimeError("Nieprawidłowa odpowiedź OpenAI (oczekiwano obiektu JSON).")
            if data.get("error"):
                raise RuntimeError(f"Błąd OpenAI: {data['error']}")
            return data


__all__ = [
    "AssistantGenerationResult",
    "AssistantRuntime",
    "AssistantRuntimeConfig",
    "count_user_messages_today",
    "detect_change_intent",
    "load_assistant_runtime_config",
]
