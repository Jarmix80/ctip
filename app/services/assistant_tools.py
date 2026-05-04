"""Narzędzia read-only dla CTIP AI Asystenta."""

from __future__ import annotations

import asyncio
import csv
import imaplib
import io
import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from email import message_from_bytes
from email.header import decode_header
from email.message import EmailMessage
from email.utils import formataddr, parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AdminUser
from app.services.admin_user_imap import load_user_imap_config
from app.services.admin_users import resolve_email_delivery_settings
from app.services.assistant_sql_guard import AssistantSqlGuardError, guard_readonly_sql
from app.services.contracts_dashboard import load_firebird_runtime_config
from app.services.email_client import send_smtp_message
from app.services.settings_store import build_store
from app.services.workflow_sheet_sync import (
    WorkflowSheetRuntimeConfig,
    _open_workbook,
    _resolve_devices_worksheet,
    load_workflow_sheet_runtime_config,
    workflow_sheet_sync_configured,
)


@dataclass(slots=True, frozen=True)
class AssistantToolResult:
    """Wynik wykonania narzędzia wraz z metadanymi audytowymi."""

    tool_name: str
    status: str
    payload: dict[str, Any]
    row_count: int | None
    generated_sql: str | None
    error_message: str | None
    duration_ms: int


@dataclass(slots=True, frozen=True)
class AssistantToolRuntimeConfig:
    """Runtime konfiguracja narzędzi asystenta."""

    firebird_row_limit: int
    sheets_row_limit: int
    imap_row_limit: int
    schema_row_limit: int
    tool_timeout_seconds: int
    firebird_read_user: str | None
    firebird_read_password: str | None
    firebird_read_role: str | None


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


def _to_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "t", "yes", "on"}


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _normalize_model_search_token(value: str) -> str:
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def _resolve_year_month_window(*, months_back: int) -> tuple[int, int, dict[str, Any]]:
    months = max(1, months_back)
    current = datetime.now(UTC)
    start_year = current.year
    start_month = current.month
    for _ in range(months - 1):
        start_month -= 1
        if start_month <= 0:
            start_month = 12
            start_year -= 1
    start_key = start_year * 100 + start_month
    end_key = current.year * 100 + current.month
    return (
        start_key,
        end_key,
        {"months_back": months, "start_yyyymm": start_key, "end_yyyymm": end_key},
    )


def _firebird_knowledge_index_path() -> Path:
    return _repo_root() / "docs" / "firebird" / "knowledge" / "firebird_ms_knowledge.json"


def _tokenize_search_text(value: str) -> list[str]:
    return [token for token in re.split(r"[^0-9A-Za-z_]+", (value or "").lower()) if token]


def _load_firebird_knowledge_index() -> dict[str, Any]:
    path = _firebird_knowledge_index_path()
    if not path.exists():
        raise RuntimeError(
            f"Brak indeksu wiedzy Firebird: {path}. "
            "Uruchom skrypt `python scripts/build_firebird_knowledge_index.py`."
        )
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise RuntimeError(
            "Nieprawidłowy format indeksu wiedzy Firebird (oczekiwano obiektu JSON)."
        )
    return data


_EMAIL_REPORT_ALLOWED_SOURCE_TOOLS = {
    "firebird_read",
    "firebird_business_read",
    "firebird_knowledge_read",
    "sheets_read",
    "imap_read",
    "ctip_schema_read",
}


class AssistantDataTools:
    """Adapter narzędzi danych dostępnych dla asystenta (wyłącznie odczyt)."""

    _SETTINGS_NAMESPACE = "assistant"

    def __init__(
        self,
        session: AsyncSession,
        *,
        settings_store_secret: str | None,
        user_id: int | None = None,
    ):
        self._session = session
        self._settings_store = build_store(settings_store_secret)
        self._user_id = user_id
        self._tool_history: list[AssistantToolResult] = []

    async def load_runtime_config(self) -> AssistantToolRuntimeConfig:
        """Wczytuje runtime konfigurację narzędzi z `admin_setting` + domyślne wartości."""
        stored = await self._settings_store.get_namespace(self._session, self._SETTINGS_NAMESPACE)
        return AssistantToolRuntimeConfig(
            firebird_row_limit=_to_int(
                stored.get("firebird_row_limit"),
                200,
                min_value=20,
                max_value=2000,
            ),
            sheets_row_limit=_to_int(
                stored.get("sheets_row_limit"),
                200,
                min_value=20,
                max_value=2000,
            ),
            imap_row_limit=_to_int(
                stored.get("imap_row_limit"),
                30,
                min_value=5,
                max_value=200,
            ),
            schema_row_limit=_to_int(
                stored.get("schema_row_limit"),
                50,
                min_value=5,
                max_value=300,
            ),
            tool_timeout_seconds=_to_int(
                stored.get("tool_timeout_seconds"),
                20,
                min_value=5,
                max_value=120,
            ),
            firebird_read_user=(stored.get("firebird_read_user") or "").strip() or None,
            firebird_read_password=stored.get("firebird_read_password"),
            firebird_read_role=(stored.get("firebird_read_role") or "").strip() or None,
        )

    async def firebird_read(
        self,
        *,
        sql: str,
        row_limit: int | None = None,
    ) -> AssistantToolResult:
        """Wykonuje bezpieczny odczyt Firebird z limitem i kontrolą czasu."""
        runtime = await self.load_runtime_config()
        runtime_fb_config = await load_firebird_runtime_config(self._session)
        limit = _to_int(
            row_limit,
            runtime.firebird_row_limit,
            min_value=1,
            max_value=runtime.firebird_row_limit,
        )
        started = time.monotonic()
        try:
            guarded = guard_readonly_sql(sql, row_limit=limit)
            result_payload = await asyncio.wait_for(
                asyncio.to_thread(
                    self._run_firebird_read_sync,
                    guarded.wrapped_sql,
                    runtime,
                    runtime_fb_config,
                    limit,
                ),
                timeout=runtime.tool_timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            return AssistantToolResult(
                tool_name="firebird_read",
                status="error",
                payload={},
                row_count=None,
                generated_sql=(guarded.wrapped_sql if "guarded" in locals() else None),
                error_message=str(exc).strip() or type(exc).__name__,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        return AssistantToolResult(
            tool_name="firebird_read",
            status="success",
            payload=result_payload,
            row_count=int(result_payload.get("row_count") or 0),
            generated_sql=guarded.wrapped_sql,
            error_message=None,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    async def firebird_business_read(
        self,
        *,
        intent: str,
        company_name: str | None = None,
        model_name: str | None = None,
        serial_number: str | None = None,
        months_back: int | None = None,
        row_limit: int | None = None,
    ) -> AssistantToolResult:
        """Wykonuje gotowe zapytania biznesowe Firebird dla naturalnych pytań użytkownika."""
        runtime = await self.load_runtime_config()
        runtime_fb_config = await load_firebird_runtime_config(self._session)
        limit = _to_int(
            row_limit,
            runtime.firebird_row_limit,
            min_value=1,
            max_value=runtime.firebird_row_limit,
        )
        normalized_intent = (intent or "").strip().lower()
        normalized_months_back = _to_int(months_back, 12, min_value=1, max_value=120)
        started = time.monotonic()
        try:
            result_payload = await asyncio.wait_for(
                asyncio.to_thread(
                    self._run_firebird_business_read_sync,
                    runtime,
                    runtime_fb_config,
                    normalized_intent,
                    company_name,
                    model_name,
                    serial_number,
                    normalized_months_back,
                    limit,
                ),
                timeout=runtime.tool_timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            return AssistantToolResult(
                tool_name="firebird_business_read",
                status="error",
                payload={},
                row_count=None,
                generated_sql=None,
                error_message=str(exc).strip() or type(exc).__name__,
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        return AssistantToolResult(
            tool_name="firebird_business_read",
            status="success",
            payload=result_payload,
            row_count=int(result_payload.get("row_count") or 0),
            generated_sql=(
                str(result_payload.get("generated_sql"))
                if result_payload.get("generated_sql") is not None
                else None
            ),
            error_message=None,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    async def firebird_knowledge_read(
        self,
        *,
        table_name: str | None = None,
        topic: str | None = None,
        include_columns: bool = True,
        row_limit: int | None = None,
    ) -> AssistantToolResult:
        """Czyta lokalny indeks wiedzy Firebird MS z repozytorium CTIP."""
        runtime = await self.load_runtime_config()
        limit = _to_int(
            row_limit,
            runtime.schema_row_limit,
            min_value=1,
            max_value=500,
        )
        started = time.monotonic()
        try:
            index = _load_firebird_knowledge_index()
            table_items = index.get("tables") if isinstance(index.get("tables"), list) else []
            docs_items = index.get("documents") if isinstance(index.get("documents"), list) else []
            normalized_table = (table_name or "").strip().upper()
            normalized_topic = (topic or "").strip()

            if normalized_table:
                exact = [
                    item
                    for item in table_items
                    if isinstance(item, dict)
                    and str(item.get("table_name") or "").strip().upper() == normalized_table
                ]
                if not exact:
                    suggestions = [
                        str(item.get("table_name") or "")
                        for item in table_items
                        if isinstance(item, dict)
                        and normalized_table in str(item.get("table_name") or "").upper()
                    ][:10]
                    raise RuntimeError(
                        f"Nie znaleziono tabeli `{normalized_table}` w indeksie wiedzy Firebird. "
                        f"Podobne: {', '.join(suggestions) if suggestions else 'brak'}."
                    )
                item = exact[0]
                columns = item.get("columns") if isinstance(item.get("columns"), list) else []
                result_payload = {
                    "mode": "table",
                    "knowledge_source": index.get("knowledge_source"),
                    "generated_at_utc": index.get("generated_at_utc"),
                    "table": {
                        "table_name": item.get("table_name"),
                        "primary_key": item.get("primary_key"),
                        "column_count_total": item.get("column_count"),
                        "description": item.get("description"),
                        "source_path": item.get("source_path"),
                        "intro": item.get("intro"),
                        "columns": (columns[:limit] if include_columns else []),
                        "columns_returned": (min(len(columns), limit) if include_columns else 0),
                    },
                    "row_count": (min(len(columns), limit) if include_columns else 1),
                    "limited_to": limit,
                }
            elif normalized_topic:
                tokens = _tokenize_search_text(normalized_topic)
                scored: list[tuple[int, dict[str, Any]]] = []

                for item in table_items:
                    if not isinstance(item, dict):
                        continue
                    haystack_parts = [
                        str(item.get("table_name") or ""),
                        str(item.get("description") or ""),
                        " ".join(item.get("intro") or []),
                    ]
                    for col in item.get("columns") or []:
                        if isinstance(col, dict):
                            haystack_parts.append(str(col.get("column_name") or ""))
                    haystack = " ".join(haystack_parts).lower()
                    score = 0
                    for token in tokens:
                        if token and token in haystack:
                            score += 3
                            if token in str(item.get("table_name") or "").lower():
                                score += 5
                    if score > 0:
                        scored.append((score, item))

                for item in docs_items:
                    if not isinstance(item, dict):
                        continue
                    content = str(item.get("content") or "")
                    score = 0
                    for token in tokens:
                        if token and token in content.lower():
                            score += 1
                    if score > 0:
                        scored.append((score, item))

                scored.sort(key=lambda pair: pair[0], reverse=True)
                matches: list[dict[str, Any]] = []
                for score, item in scored[:limit]:
                    if "table_name" in item:
                        matches.append(
                            {
                                "kind": "table",
                                "score": score,
                                "table_name": item.get("table_name"),
                                "description": item.get("description"),
                                "source_path": item.get("source_path"),
                                "key_columns": [
                                    col.get("column_name")
                                    for col in (item.get("columns") or [])[:10]
                                    if isinstance(col, dict)
                                ],
                            }
                        )
                    else:
                        matches.append(
                            {
                                "kind": str(item.get("kind") or "doc"),
                                "score": score,
                                "title": item.get("title"),
                                "source_path": item.get("source_path"),
                                "excerpt": str(item.get("content") or "")[:1200],
                            }
                        )
                result_payload = {
                    "mode": "search",
                    "knowledge_source": index.get("knowledge_source"),
                    "generated_at_utc": index.get("generated_at_utc"),
                    "topic": normalized_topic,
                    "matches": matches,
                    "row_count": len(matches),
                    "limited_to": limit,
                }
            else:
                table_names = [
                    str(item.get("table_name") or "")
                    for item in table_items
                    if isinstance(item, dict)
                ]
                result_payload = {
                    "mode": "catalog",
                    "knowledge_source": index.get("knowledge_source"),
                    "generated_at_utc": index.get("generated_at_utc"),
                    "table_count": len(table_names),
                    "tables": table_names[:limit],
                    "row_count": min(len(table_names), limit),
                    "limited_to": limit,
                }
        except Exception as exc:  # noqa: BLE001
            return AssistantToolResult(
                tool_name="firebird_knowledge_read",
                status="error",
                payload={},
                row_count=None,
                generated_sql=None,
                error_message=str(exc).strip() or type(exc).__name__,
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        return AssistantToolResult(
            tool_name="firebird_knowledge_read",
            status="success",
            payload=result_payload,
            row_count=int(result_payload.get("row_count") or 0),
            generated_sql=None,
            error_message=None,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    async def sheets_read(
        self,
        *,
        worksheet: str | None = None,
        range_name: str | None = None,
        row_limit: int | None = None,
    ) -> AssistantToolResult:
        """Wykonuje odczyt z arkusza Google w scope tylko do odczytu."""
        runtime = await self.load_runtime_config()
        workflow_runtime_config = await load_workflow_sheet_runtime_config(self._session)
        limit = _to_int(
            row_limit,
            runtime.sheets_row_limit,
            min_value=1,
            max_value=runtime.sheets_row_limit,
        )
        started = time.monotonic()
        try:
            result_payload = await asyncio.wait_for(
                asyncio.to_thread(
                    self._run_sheets_read_sync,
                    workflow_runtime_config,
                    worksheet,
                    range_name,
                    limit,
                ),
                timeout=runtime.tool_timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            return AssistantToolResult(
                tool_name="sheets_read",
                status="error",
                payload={},
                row_count=None,
                generated_sql=None,
                error_message=str(exc).strip() or type(exc).__name__,
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        return AssistantToolResult(
            tool_name="sheets_read",
            status="success",
            payload=result_payload,
            row_count=int(result_payload.get("row_count") or 0),
            generated_sql=None,
            error_message=None,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    async def imap_read(
        self,
        *,
        folder: str | None = None,
        unread_only: bool = True,
        since_days: int | None = None,
        row_limit: int | None = None,
    ) -> AssistantToolResult:
        """Wykonuje odczyt nagłówków wiadomości IMAP przypisanej do użytkownika."""
        runtime = await self.load_runtime_config()
        limit = _to_int(
            row_limit,
            runtime.imap_row_limit,
            min_value=1,
            max_value=runtime.imap_row_limit,
        )
        normalized_since_days = (
            _to_int(since_days, 14, min_value=0, max_value=3650) if since_days is not None else 14
        )
        started = time.monotonic()
        try:
            if self._user_id is None:
                raise RuntimeError("Brak kontekstu użytkownika dla narzędzia IMAP.")
            user_stmt = select(AdminUser).where(AdminUser.id == self._user_id)
            user = (await self._session.execute(user_stmt)).scalar_one_or_none()
            user_email = user.email if user is not None else None
            imap_config = await load_user_imap_config(
                self._session,
                user_id=self._user_id,
                fallback_email=user_email,
                include_secret=True,
            )
            if not imap_config.enabled:
                raise RuntimeError("Dostęp IMAP użytkownika jest wyłączony.")
            host = (imap_config.host or "").strip()
            email_address = (imap_config.email or "").strip()
            username = (imap_config.username or email_address).strip()
            password = imap_config.password or ""
            target_folder = (folder or imap_config.folder or "INBOX").strip() or "INBOX"
            if not host:
                raise RuntimeError("Brak hosta IMAP w konfiguracji użytkownika.")
            if not username:
                raise RuntimeError("Brak loginu IMAP w konfiguracji użytkownika.")
            if not password:
                raise RuntimeError("Brak hasła IMAP w konfiguracji użytkownika.")
            port = imap_config.port or (993 if imap_config.use_ssl else 143)
            result_payload = await asyncio.wait_for(
                asyncio.to_thread(
                    self._run_imap_read_sync,
                    host,
                    int(port),
                    username,
                    password,
                    bool(imap_config.use_ssl),
                    target_folder,
                    bool(unread_only),
                    int(normalized_since_days),
                    limit,
                ),
                timeout=runtime.tool_timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            return AssistantToolResult(
                tool_name="imap_read",
                status="error",
                payload={},
                row_count=None,
                generated_sql=None,
                error_message=str(exc).strip() or type(exc).__name__,
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        return AssistantToolResult(
            tool_name="imap_read",
            status="success",
            payload=result_payload,
            row_count=int(result_payload.get("row_count") or 0),
            generated_sql=None,
            error_message=None,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    async def ctip_schema_read(
        self,
        *,
        table_name: str | None = None,
        include_columns: bool = True,
        include_relationships: bool = True,
        row_limit: int | None = None,
    ) -> AssistantToolResult:
        """Zwraca metadane schematu PostgreSQL `ctip` (tabele, kolumny, relacje FK)."""
        runtime = await self.load_runtime_config()
        limit = _to_int(
            row_limit,
            runtime.schema_row_limit,
            min_value=1,
            max_value=runtime.schema_row_limit,
        )
        normalized_table = (table_name or "").strip().lower() or None
        started = time.monotonic()
        try:
            if normalized_table:
                tables_stmt = text(
                    """
                    SELECT t.table_name
                    FROM information_schema.tables t
                    WHERE t.table_schema = :schema_name
                      AND t.table_type = 'BASE TABLE'
                      AND lower(t.table_name) = :table_name
                    ORDER BY t.table_name
                    LIMIT :row_limit
                    """
                )
                table_rows = await self._session.execute(
                    tables_stmt,
                    {
                        "schema_name": "ctip",
                        "table_name": normalized_table,
                        "row_limit": limit,
                    },
                )
            else:
                tables_stmt = text(
                    """
                    SELECT t.table_name
                    FROM information_schema.tables t
                    WHERE t.table_schema = :schema_name
                      AND t.table_type = 'BASE TABLE'
                    ORDER BY t.table_name
                    LIMIT :row_limit
                    """
                )
                table_rows = await self._session.execute(
                    tables_stmt,
                    {
                        "schema_name": "ctip",
                        "row_limit": limit,
                    },
                )
            table_names = [str(row[0]) for row in table_rows.all()]

            tables_payload: list[dict[str, Any]] = []
            for current_table in table_names:
                item: dict[str, Any] = {"table_name": current_table}
                if include_columns:
                    item["columns"] = await self._load_schema_columns(current_table)
                if include_relationships:
                    item["outgoing_relationships"] = await self._load_schema_outgoing_relationships(
                        current_table
                    )
                    item["incoming_relationships"] = await self._load_schema_incoming_relationships(
                        current_table
                    )
                tables_payload.append(item)

            result_payload = {
                "schema": "ctip",
                "table_count": len(tables_payload),
                "limited_to": limit,
                "filter_table_name": normalized_table,
                "tables": tables_payload,
            }
        except Exception as exc:  # noqa: BLE001
            return AssistantToolResult(
                tool_name="ctip_schema_read",
                status="error",
                payload={},
                row_count=None,
                generated_sql=None,
                error_message=str(exc).strip() or type(exc).__name__,
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        return AssistantToolResult(
            tool_name="ctip_schema_read",
            status="success",
            payload=result_payload,
            row_count=len(tables_payload),
            generated_sql=None,
            error_message=None,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    async def email_send_report(
        self,
        *,
        recipient_email: str,
        subject: str | None = None,
        message_body: str | None = None,
        report_format: str | None = None,
        source_tool: str | None = None,
        report_title: str | None = None,
    ) -> AssistantToolResult:
        """Wysyła raport jako załącznik przez systemową skrzynkę SMTP CTIP."""
        runtime = await self.load_runtime_config()
        started = time.monotonic()
        try:
            _, parsed_recipient = parseaddr((recipient_email or "").strip())
            if not parsed_recipient or "@" not in parsed_recipient:
                raise RuntimeError("Nieprawidłowy adres odbiorcy e-mail.")

            if source_tool is not None and source_tool.strip():
                normalized_source_tool = source_tool.strip()
                if normalized_source_tool not in _EMAIL_REPORT_ALLOWED_SOURCE_TOOLS:
                    raise RuntimeError(
                        "Niedozwolone źródło raportu. "
                        f"Dozwolone: {', '.join(sorted(_EMAIL_REPORT_ALLOWED_SOURCE_TOOLS))}."
                    )
            else:
                normalized_source_tool = None

            delivery = await resolve_email_delivery_settings(self._session)
            if delivery is None:
                raise RuntimeError(
                    "Brak konfiguracji SMTP systemowej CTIP. Uzupełnij `admin/config/email`."
                )

            source_result = self._pick_report_source_result(normalized_source_tool)
            if source_result is None:
                if normalized_source_tool:
                    raise RuntimeError(
                        f"Brak wcześniejszego wyniku narzędzia `{normalized_source_tool}` w tej rozmowie."
                    )
                raise RuntimeError(
                    "Brak danych do raportu. Najpierw wykonaj zapytanie (np. firebird_business_read)."
                )

            normalized_report_format = (report_format or "csv").strip().lower()
            attachment_name, attachment_bytes = self._build_report_attachment(
                source_result=source_result,
                report_format=normalized_report_format,
                report_title=report_title,
            )
            if len(attachment_bytes) > 5 * 1024 * 1024:
                raise RuntimeError("Załącznik raportu przekracza limit 5 MB.")

            sender_name = (delivery.sender_name or "").strip() or "CTIP Asystent"
            email_subject = (
                subject or ""
            ).strip() or f"CTIP raport: {source_result.tool_name} ({datetime.now(UTC).date().isoformat()})"
            text_body = (message_body or "").strip()
            if not text_body:
                text_body = (
                    "Automatyczny raport wygenerowany przez CTIP AI Asystenta.\n\n"
                    f"Źródło danych: {source_result.tool_name}\n"
                    f"Liczba rekordów: {source_result.row_count if source_result.row_count is not None else 'n/d'}\n"
                    f"Wygenerowano: {datetime.now(UTC).isoformat()}"
                )

            message = EmailMessage()
            message["From"] = formataddr((sender_name, delivery.sender_address))
            message["To"] = parsed_recipient
            message["Subject"] = email_subject
            message.set_content(text_body)

            if normalized_report_format == "json":
                maintype, subtype = "application", "json"
            elif normalized_report_format == "txt":
                maintype, subtype = "text", "plain"
            else:
                maintype, subtype = "text", "csv"
            message.add_attachment(
                attachment_bytes,
                maintype=maintype,
                subtype=subtype,
                filename=attachment_name,
            )

            send_result = await asyncio.wait_for(
                send_smtp_message(
                    host=delivery.host,
                    port=delivery.port,
                    username=delivery.username,
                    password=delivery.password,
                    use_tls=delivery.use_tls,
                    use_ssl=delivery.use_ssl,
                    message=message,
                ),
                timeout=runtime.tool_timeout_seconds,
            )
            if not send_result.success:
                raise RuntimeError(send_result.message or "Wysyłka e-mail zakończona błędem.")

            result_payload = {
                "recipient_email": parsed_recipient,
                "subject": email_subject,
                "source_tool": source_result.tool_name,
                "source_row_count": source_result.row_count,
                "attachment_name": attachment_name,
                "attachment_size_bytes": len(attachment_bytes),
                "report_format": normalized_report_format,
                "message": send_result.message,
            }
        except Exception as exc:  # noqa: BLE001
            return AssistantToolResult(
                tool_name="email_send_report",
                status="error",
                payload={},
                row_count=None,
                generated_sql=None,
                error_message=str(exc).strip() or type(exc).__name__,
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        return AssistantToolResult(
            tool_name="email_send_report",
            status="success",
            payload=result_payload,
            row_count=source_result.row_count,
            generated_sql=None,
            error_message=None,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    def _pick_report_source_result(self, source_tool: str | None) -> AssistantToolResult | None:
        for item in reversed(self._tool_history):
            if item.status != "success":
                continue
            if item.tool_name not in _EMAIL_REPORT_ALLOWED_SOURCE_TOOLS:
                continue
            if source_tool and item.tool_name != source_tool:
                continue
            return item
        return None

    def _build_report_attachment(
        self,
        *,
        source_result: AssistantToolResult,
        report_format: str,
        report_title: str | None,
    ) -> tuple[str, bytes]:
        payload = source_result.payload if isinstance(source_result.payload, dict) else {}
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        safe_title = re.sub(r"[^0-9A-Za-z_.-]+", "_", (report_title or "").strip()).strip("_")
        base_name = safe_title or f"ctip_{source_result.tool_name}_report"

        if report_format == "json":
            content = json.dumps(
                {
                    "tool": source_result.tool_name,
                    "row_count": source_result.row_count,
                    "generated_sql": source_result.generated_sql,
                    "payload": payload,
                },
                ensure_ascii=False,
                indent=2,
            )
            return f"{base_name}_{timestamp}.json", content.encode("utf-8")

        if report_format == "txt":
            lines = [
                "Raport CTIP AI Asystent",
                f"Źródło: {source_result.tool_name}",
                f"Liczba rekordów: {source_result.row_count if source_result.row_count is not None else 'n/d'}",
                f"Data UTC: {datetime.now(UTC).isoformat()}",
                "",
            ]
            rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
            if rows:
                for index, row in enumerate(rows[:200], start=1):
                    if isinstance(row, dict):
                        row_text = ", ".join(
                            f"{key}={_json_safe(value)}" for key, value in row.items()
                        )
                    else:
                        row_text = str(row)
                    lines.append(f"{index}. {row_text}")
            else:
                lines.append(json.dumps(payload, ensure_ascii=False, indent=2))
            return f"{base_name}_{timestamp}.txt", "\n".join(lines).encode("utf-8")

        if report_format != "csv":
            raise RuntimeError("Nieobsługiwany format raportu. Dozwolone: csv, json, txt.")

        rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
        columns = payload.get("columns") if isinstance(payload.get("columns"), list) else []
        csv_buffer = io.StringIO(newline="")
        writer: csv.writer | csv.DictWriter[str]

        if columns:
            fieldnames = [str(column) for column in columns]
            writer = csv.DictWriter(csv_buffer, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                if isinstance(row, dict):
                    writer.writerow({key: _json_safe(row.get(key)) for key in fieldnames})
                else:
                    writer.writerow({fieldnames[0]: _json_safe(row)})
        elif rows and all(isinstance(row, dict) for row in rows):
            first_row = rows[0]
            fieldnames = [str(key) for key in first_row.keys()]
            writer = csv.DictWriter(csv_buffer, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                row_dict = row if isinstance(row, dict) else {}
                writer.writerow({key: _json_safe(row_dict.get(key)) for key in fieldnames})
        else:
            writer = csv.writer(csv_buffer)
            writer.writerow(["key", "value"])
            for key in sorted(payload.keys()):
                writer.writerow([key, json.dumps(_json_safe(payload.get(key)), ensure_ascii=False)])

        return f"{base_name}_{timestamp}.csv", csv_buffer.getvalue().encode("utf-8")

    async def _load_schema_columns(self, table_name: str) -> list[dict[str, Any]]:
        columns_stmt = text(
            """
            SELECT
                c.column_name,
                c.data_type,
                c.is_nullable,
                c.column_default
            FROM information_schema.columns c
            WHERE c.table_schema = :schema_name
              AND c.table_name = :table_name
            ORDER BY c.ordinal_position
            """
        )
        rows = await self._session.execute(
            columns_stmt,
            {
                "schema_name": "ctip",
                "table_name": table_name,
            },
        )
        columns: list[dict[str, Any]] = []
        for row in rows.all():
            columns.append(
                {
                    "column_name": str(row[0]),
                    "data_type": str(row[1]),
                    "is_nullable": str(row[2]) == "YES",
                    "column_default": row[3] if row[3] is None else str(row[3]),
                }
            )
        return columns

    async def _load_schema_outgoing_relationships(self, table_name: str) -> list[dict[str, Any]]:
        outgoing_stmt = text(
            """
            SELECT
                tc.constraint_name,
                kcu.column_name,
                ccu.table_name AS referenced_table,
                ccu.column_name AS referenced_column
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
              ON tc.constraint_name = ccu.constraint_name
             AND tc.table_schema = ccu.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema = :schema_name
              AND tc.table_name = :table_name
            ORDER BY tc.constraint_name, kcu.ordinal_position
            """
        )
        rows = await self._session.execute(
            outgoing_stmt,
            {
                "schema_name": "ctip",
                "table_name": table_name,
            },
        )
        relationships: list[dict[str, Any]] = []
        for row in rows.all():
            relationships.append(
                {
                    "constraint_name": str(row[0]),
                    "column_name": str(row[1]),
                    "referenced_table": str(row[2]),
                    "referenced_column": str(row[3]),
                }
            )
        return relationships

    async def _load_schema_incoming_relationships(self, table_name: str) -> list[dict[str, Any]]:
        incoming_stmt = text(
            """
            SELECT
                tc.constraint_name,
                kcu.table_name AS source_table,
                kcu.column_name AS source_column,
                ccu.column_name AS referenced_column
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
              ON tc.constraint_name = ccu.constraint_name
             AND tc.table_schema = ccu.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema = :schema_name
              AND ccu.table_name = :table_name
            ORDER BY tc.constraint_name, kcu.ordinal_position
            """
        )
        rows = await self._session.execute(
            incoming_stmt,
            {
                "schema_name": "ctip",
                "table_name": table_name,
            },
        )
        relationships: list[dict[str, Any]] = []
        for row in rows.all():
            relationships.append(
                {
                    "constraint_name": str(row[0]),
                    "source_table": str(row[1]),
                    "source_column": str(row[2]),
                    "referenced_column": str(row[3]),
                }
            )
        return relationships

    @staticmethod
    def _decode_header_value(raw: str | None) -> str:
        if not raw:
            return ""
        parts = decode_header(raw)
        rendered: list[str] = []
        for value, encoding in parts:
            if isinstance(value, bytes):
                try:
                    rendered.append(value.decode(encoding or "utf-8", errors="replace"))
                except LookupError:
                    rendered.append(value.decode("utf-8", errors="replace"))
            else:
                rendered.append(value)
        return "".join(rendered).strip()

    def _run_imap_read_sync(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        use_ssl: bool,
        folder: str,
        unread_only: bool,
        since_days: int,
        row_limit: int,
    ) -> dict[str, Any]:
        criteria: list[str] = ["UNSEEN" if unread_only else "ALL"]
        if since_days > 0:
            since_token = (datetime.now(UTC) - timedelta(days=since_days)).strftime("%d-%b-%Y")
            criteria.extend(["SINCE", since_token])

        if use_ssl:
            client: imaplib.IMAP4 = imaplib.IMAP4_SSL(host, port, timeout=15)
        else:
            client = imaplib.IMAP4(host, port, timeout=15)

        with client:
            login_status, _ = client.login(username, password)
            if login_status != "OK":
                raise RuntimeError("Logowanie IMAP zakończone niepowodzeniem.")
            select_status, _ = client.select(folder, readonly=True)
            if select_status != "OK":
                raise RuntimeError(f"Nie udało się otworzyć folderu IMAP: {folder}.")
            search_status, search_data = client.search(None, *criteria)
            if search_status != "OK":
                raise RuntimeError("Wyszukiwanie wiadomości IMAP zakończone błędem.")

            raw_ids = search_data[0] if search_data else b""
            message_ids = raw_ids.split() if isinstance(raw_ids, bytes) else []
            selected_ids = message_ids[-row_limit:][::-1]
            messages: list[dict[str, Any]] = []
            for raw_id in selected_ids:
                fetch_status, fetch_data = client.fetch(
                    raw_id,
                    "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM TO DATE MESSAGE-ID)])",
                )
                if fetch_status != "OK" or not fetch_data:
                    continue
                header_bytes = b""
                for chunk in fetch_data:
                    if isinstance(chunk, tuple) and len(chunk) >= 2 and isinstance(chunk[1], bytes):
                        header_bytes = chunk[1]
                        break
                if not header_bytes:
                    continue
                message = message_from_bytes(header_bytes)
                raw_date = self._decode_header_value(message.get("Date"))
                parsed_date = None
                if raw_date:
                    try:
                        parsed = parsedate_to_datetime(raw_date)
                        parsed_date = parsed.isoformat()
                    except (TypeError, ValueError, IndexError):
                        parsed_date = raw_date
                messages.append(
                    {
                        "message_seq": raw_id.decode("utf-8", errors="ignore"),
                        "subject": self._decode_header_value(message.get("Subject")),
                        "from": self._decode_header_value(message.get("From")),
                        "to": self._decode_header_value(message.get("To")),
                        "date": parsed_date,
                        "message_id": self._decode_header_value(message.get("Message-ID")),
                    }
                )

        return {
            "folder": folder,
            "criteria": criteria,
            "row_count": len(messages),
            "limited_to": row_limit,
            "messages": messages,
        }

    def _build_contract_settlement_knowledge_payload(self) -> dict[str, Any]:
        table_refs: list[dict[str, Any]] = []
        document_refs: list[dict[str, Any]] = []
        generated_at_utc: str | None = None
        knowledge_error: str | None = None

        try:
            index = _load_firebird_knowledge_index()
            generated_at_utc = str(index.get("generated_at_utc") or "") or None
            tables = index.get("tables") if isinstance(index.get("tables"), list) else []
            documents = index.get("documents") if isinstance(index.get("documents"), list) else []
            tracked_tables = {"UMOWA", "UMOWACPC", "CPC"}
            tracked_columns = {
                "UMOWA": {"DATA_START", "DATA_STOP", "DATA"},
                "UMOWACPC": {"U_START", "U_STOP", "AKTYWNA", "ROZLICZ"},
                "CPC": {"ROK", "MIESIAC", "ID_UMOWACPC", "ID_FAKTURA"},
            }
            for item in tables:
                if not isinstance(item, dict):
                    continue
                table_name = str(item.get("table_name") or "").strip().upper()
                if table_name not in tracked_tables:
                    continue
                columns = item.get("columns") if isinstance(item.get("columns"), list) else []
                interesting_columns = [
                    str(column.get("column_name") or "")
                    for column in columns
                    if isinstance(column, dict)
                    and str(column.get("column_name") or "").strip().upper()
                    in tracked_columns.get(table_name, set())
                ]
                table_refs.append(
                    {
                        "table_name": table_name,
                        "source_path": item.get("source_path"),
                        "interesting_columns": interesting_columns,
                    }
                )

            tracked_docs = (
                "faktury_umowy.md",
                "run_C508P203417_nov2025.md",
                "cpc_update_warning.md",
            )
            for item in documents:
                if not isinstance(item, dict):
                    continue
                source_path = str(item.get("source_path") or "").strip()
                if not source_path:
                    continue
                if not any(source_path.endswith(doc_name) for doc_name in tracked_docs):
                    continue
                document_refs.append(
                    {
                        "title": item.get("title"),
                        "source_path": source_path,
                    }
                )
        except Exception as exc:  # noqa: BLE001
            knowledge_error = str(exc).strip() or type(exc).__name__

        summary = (
            "Rozliczanie umów w MS działa okresowo: ważność umowy określają zakresy dat "
            "(UMOWA.DATA_START/DATA_STOP oraz UMOWACPC.U_START/U_STOP), a właściwe rozliczenie "
            "jest miesięczne w tabeli CPC (ROK/MIESIAC) z powiązaniem do faktury przez ID_FAKTURA."
        )
        rules = [
            "Filtr aktywności umowy opiera się o zakres dat obowiązywania, a nie pojedynczą datę.",
            "Rozliczenie kopii/liczników odbywa się w okresach miesięcznych (CPC.ROK + CPC.MIESIAC).",
            "Powiązanie rozliczenia z dokumentem sprzedaży realizuje CPC.ID_FAKTURA -> FAKTURA.ID_FAKTURA_TABLE.",
            "Data wystawienia faktury może być późniejsza niż miesiąc rozliczeniowy CPC (rozliczenia wsteczne).",
            "Przy raportach okresowych stosuj zakresy START:END i agregację po miesiącach, nie po jednej dacie.",
        ]

        return {
            "intent": "contract_settlement_period_explainer",
            "summary": summary,
            "rules": rules,
            "table_references": table_refs,
            "document_references": document_refs,
            "knowledge_generated_at_utc": generated_at_utc,
            "knowledge_error": knowledge_error,
            "row_count": len(rules),
        }

    def _build_firebird_connect_kwargs(
        self,
        runtime_settings: AssistantToolRuntimeConfig,
        runtime_fb_config,
    ) -> dict[str, Any]:
        user = runtime_settings.firebird_read_user or runtime_fb_config.user
        password = runtime_settings.firebird_read_password or runtime_fb_config.password
        role = runtime_settings.firebird_read_role or runtime_fb_config.role
        kwargs: dict[str, Any] = {
            "port": runtime_fb_config.port,
            "user": user,
            "password": password or "",
            "charset": runtime_fb_config.charset,
        }
        if role:
            kwargs["role"] = role
        return kwargs

    def _open_firebird_connection(
        self, runtime_settings: AssistantToolRuntimeConfig, runtime_fb_config
    ):
        import firebirdsql  # type: ignore[import-not-found]

        kwargs = self._build_firebird_connect_kwargs(runtime_settings, runtime_fb_config)
        if runtime_fb_config.mode == "network":
            if not runtime_fb_config.host:
                raise RuntimeError("Brak hosta Firebird w aktywnej konfiguracji.")
            if not runtime_fb_config.database:
                raise RuntimeError("Brak bazy Firebird w aktywnej konfiguracji.")
            return firebirdsql.connect(
                host=runtime_fb_config.host,
                database=runtime_fb_config.database,
                **kwargs,
            )

        db_path = Path(runtime_fb_config.local_copy_path)
        if not db_path.is_absolute():
            db_path = _repo_root() / db_path
        if not db_path.exists():
            raise RuntimeError(f"Brak lokalnej kopii Firebird: {db_path}")
        return firebirdsql.connect(host="127.0.0.1", database=str(db_path), **kwargs)

    @staticmethod
    def _fetch_cursor_rows(cursor, row_limit: int) -> tuple[list[str], list[dict[str, Any]]]:
        columns = [str(desc[0]) for desc in (cursor.description or [])]
        rows = cursor.fetchmany(row_limit)
        mapped_rows = []
        for row in rows:
            mapped_row = {columns[i]: _json_safe(row[i]) for i in range(len(columns))}
            mapped_rows.append(mapped_row)
        return columns, mapped_rows

    def _run_firebird_read_sync(
        self,
        wrapped_sql: str,
        runtime_settings: AssistantToolRuntimeConfig,
        runtime_fb_config,
        row_limit: int,
    ) -> dict[str, Any]:
        connection = self._open_firebird_connection(runtime_settings, runtime_fb_config)

        cursor = None
        try:
            cursor = connection.cursor()
            try:
                cursor.execute("SET TRANSACTION READ ONLY")
            except Exception:  # noqa: BLE001
                # fallback - konto read-only + guard SQL nadal blokują zapisy
                pass
            cursor.execute(wrapped_sql)
            columns, mapped_rows = self._fetch_cursor_rows(cursor, row_limit)
            return {
                "columns": columns,
                "rows": mapped_rows,
                "row_count": len(mapped_rows),
                "limited_to": row_limit,
            }
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:  # pragma: no cover
                    pass
            try:
                connection.close()
            except Exception:  # pragma: no cover
                pass

    def _run_firebird_business_read_sync(
        self,
        runtime_settings: AssistantToolRuntimeConfig,
        runtime_fb_config,
        intent: str,
        company_name: str | None,
        model_name: str | None,
        serial_number: str | None,
        months_back: int,
        row_limit: int,
    ) -> dict[str, Any]:
        if intent == "contract_settlement_period_explainer":
            return self._build_contract_settlement_knowledge_payload()

        connection = self._open_firebird_connection(runtime_settings, runtime_fb_config)
        cursor = None
        try:
            cursor = connection.cursor()
            try:
                cursor.execute("SET TRANSACTION READ ONLY")
            except Exception:  # noqa: BLE001
                pass

            if intent == "devices_by_company":
                company = (company_name or "").strip()
                if not company:
                    raise RuntimeError(
                        "Dla intent=devices_by_company wymagane jest `company_name`."
                    )
                sql = """
                    SELECT
                        m.ID_MASZYNA,
                        m.ID_KLIENT,
                        TRIM(COALESCE(k.NAZWA, '')) AS KLIENT_NAZWA,
                        TRIM(COALESCE(m.MARKA, '')) AS MARKA,
                        TRIM(COALESCE(m.MODEL, '')) AS MODEL,
                        TRIM(COALESCE(m.SERIAL, '')) AS SERIAL,
                        TRIM(COALESCE(m.EWIDENCJA, '')) AS EWIDENCJA,
                        TRIM(COALESCE(m.AKTYWNA, '')) AS AKTYWNA
                    FROM MASZYNA m
                    JOIN KLIENT k ON k.ID_KLIENT = m.ID_KLIENT
                    WHERE UPPER(COALESCE(k.NAZWA, '')) CONTAINING ?
                    ORDER BY m.ID_MASZYNA DESC
                """
                cursor.execute(sql, (company.upper(),))
                columns, mapped_rows = self._fetch_cursor_rows(cursor, row_limit)
                return {
                    "intent": intent,
                    "criteria": {"company_name": company},
                    "columns": columns,
                    "rows": mapped_rows,
                    "row_count": len(mapped_rows),
                    "limited_to": row_limit,
                    "generated_sql": sql.strip(),
                }

            if intent == "company_monthly_print_summary":
                company = (company_name or "").strip()
                if not company:
                    raise RuntimeError(
                        "Dla intent=company_monthly_print_summary wymagane jest `company_name`."
                    )
                start_key, end_key, period_criteria = _resolve_year_month_window(
                    months_back=months_back
                )
                sql = """
                    WITH cpc_base AS (
                        SELECT
                            c.ID_MASZYNA,
                            c.ROK AS ROK,
                            COALESCE(NULLIF(c.MIESIAC, 0), 1) AS MIESIAC,
                            (
                                COALESCE(c.LICZNIK_MONO_END, c.LICZNIK_MONO_START, 0)
                                - COALESCE(c.LICZNIK_MONO_START, c.LICZNIK_MONO_END, 0)
                            ) AS MONO_DIFF,
                            (
                                COALESCE(c.LICZNIK_KOLOR_END, c.LICZNIK_KOLOR_START, 0)
                                - COALESCE(c.LICZNIK_KOLOR_START, c.LICZNIK_KOLOR_END, 0)
                            ) AS KOLOR_DIFF
                        FROM CPC c
                        JOIN MASZYNA m ON m.ID_MASZYNA = c.ID_MASZYNA
                        JOIN KLIENT k ON k.ID_KLIENT = m.ID_KLIENT
                        WHERE UPPER(COALESCE(k.NAZWA, '')) CONTAINING ?
                          AND ((c.ROK * 100) + COALESCE(NULLIF(c.MIESIAC, 0), 1)) BETWEEN ? AND ?
                    )
                    SELECT
                        b.ROK,
                        b.MIESIAC,
                        COUNT(*) AS REKORDY_CPC,
                        COUNT(DISTINCT b.ID_MASZYNA) AS LICZBA_URZADZEN,
                        SUM(b.MONO_DIFF) AS MONO_SUM,
                        SUM(b.KOLOR_DIFF) AS KOLOR_SUM,
                        SUM(b.MONO_DIFF + b.KOLOR_DIFF) AS LACZNIE_SUM,
                        CASE
                            WHEN COUNT(DISTINCT b.ID_MASZYNA) = 0 THEN NULL
                            ELSE SUM(b.MONO_DIFF + b.KOLOR_DIFF) * 1.0 / COUNT(DISTINCT b.ID_MASZYNA)
                        END AS SREDNIO_NA_URZADZENIE
                    FROM cpc_base b
                    GROUP BY b.ROK, b.MIESIAC
                    ORDER BY b.ROK DESC, b.MIESIAC DESC
                """
                cursor.execute(sql, (company.upper(), start_key, end_key))
                effective_row_limit = min(
                    max(row_limit, months_back),
                    runtime_settings.firebird_row_limit,
                )
                columns, mapped_rows = self._fetch_cursor_rows(cursor, effective_row_limit)
                return {
                    "intent": intent,
                    "criteria": {"company_name": company, **period_criteria},
                    "columns": columns,
                    "rows": mapped_rows,
                    "row_count": len(mapped_rows),
                    "limited_to": effective_row_limit,
                    "generated_sql": sql.strip(),
                }

            if intent == "monthly_average_print_by_model":
                model = (model_name or "").strip()
                if not model:
                    raise RuntimeError(
                        "Dla intent=monthly_average_print_by_model wymagane jest `model_name`."
                    )
                model_token = _normalize_model_search_token(model)
                if not model_token:
                    raise RuntimeError("`model_name` nie zawiera znaków możliwych do wyszukania.")
                start_key, end_key, period_criteria = _resolve_year_month_window(
                    months_back=months_back
                )
                sql = """
                    WITH cpc_base AS (
                        SELECT
                            c.ID_MASZYNA,
                            c.ROK AS ROK,
                            COALESCE(NULLIF(c.MIESIAC, 0), 1) AS MIESIAC,
                            (
                                COALESCE(c.LICZNIK_MONO_END, c.LICZNIK_MONO_START, 0)
                                - COALESCE(c.LICZNIK_MONO_START, c.LICZNIK_MONO_END, 0)
                            ) AS MONO_DIFF,
                            (
                                COALESCE(c.LICZNIK_KOLOR_END, c.LICZNIK_KOLOR_START, 0)
                                - COALESCE(c.LICZNIK_KOLOR_START, c.LICZNIK_KOLOR_END, 0)
                            ) AS KOLOR_DIFF
                        FROM CPC c
                        JOIN MASZYNA m ON m.ID_MASZYNA = c.ID_MASZYNA
                        WHERE UPPER(
                            REPLACE(REPLACE(REPLACE(COALESCE(m.MODEL, ''), ' ', ''), '-', ''), '/', '')
                        ) CONTAINING ?
                          AND ((c.ROK * 100) + COALESCE(NULLIF(c.MIESIAC, 0), 1)) BETWEEN ? AND ?
                    )
                    SELECT
                        b.ROK,
                        b.MIESIAC,
                        COUNT(*) AS REKORDY_CPC,
                        COUNT(DISTINCT b.ID_MASZYNA) AS LICZBA_URZADZEN,
                        SUM(b.MONO_DIFF) AS MONO_SUM,
                        SUM(b.KOLOR_DIFF) AS KOLOR_SUM,
                        SUM(b.MONO_DIFF + b.KOLOR_DIFF) AS LACZNIE_SUM,
                        AVG(b.MONO_DIFF + b.KOLOR_DIFF) AS SREDNIO_NA_REKORD_CPC,
                        CASE
                            WHEN COUNT(DISTINCT b.ID_MASZYNA) = 0 THEN NULL
                            ELSE SUM(b.MONO_DIFF + b.KOLOR_DIFF) * 1.0 / COUNT(DISTINCT b.ID_MASZYNA)
                        END AS SREDNIO_NA_URZADZENIE
                    FROM cpc_base b
                    GROUP BY b.ROK, b.MIESIAC
                    ORDER BY b.ROK DESC, b.MIESIAC DESC
                """
                cursor.execute(sql, (model_token, start_key, end_key))
                effective_row_limit = min(
                    max(row_limit, months_back),
                    runtime_settings.firebird_row_limit,
                )
                columns, mapped_rows = self._fetch_cursor_rows(cursor, effective_row_limit)
                return {
                    "intent": intent,
                    "criteria": {
                        "model_name": model,
                        "normalized_model_token": model_token,
                        **period_criteria,
                    },
                    "columns": columns,
                    "rows": mapped_rows,
                    "row_count": len(mapped_rows),
                    "limited_to": effective_row_limit,
                    "generated_sql": sql.strip(),
                }

            if intent == "top_models_by_volume":
                start_key, end_key, period_criteria = _resolve_year_month_window(
                    months_back=months_back
                )
                sql = """
                    WITH cpc_base AS (
                        SELECT
                            c.ID_MASZYNA,
                            TRIM(COALESCE(m.MODEL, '')) AS MODEL,
                            (
                                COALESCE(c.LICZNIK_MONO_END, c.LICZNIK_MONO_START, 0)
                                - COALESCE(c.LICZNIK_MONO_START, c.LICZNIK_MONO_END, 0)
                            ) AS MONO_DIFF,
                            (
                                COALESCE(c.LICZNIK_KOLOR_END, c.LICZNIK_KOLOR_START, 0)
                                - COALESCE(c.LICZNIK_KOLOR_START, c.LICZNIK_KOLOR_END, 0)
                            ) AS KOLOR_DIFF
                        FROM CPC c
                        JOIN MASZYNA m ON m.ID_MASZYNA = c.ID_MASZYNA
                        WHERE ((c.ROK * 100) + COALESCE(NULLIF(c.MIESIAC, 0), 1)) BETWEEN ? AND ?
                    )
                    SELECT
                        b.MODEL,
                        COUNT(*) AS REKORDY_CPC,
                        COUNT(DISTINCT b.ID_MASZYNA) AS LICZBA_URZADZEN,
                        SUM(b.MONO_DIFF) AS MONO_SUM,
                        SUM(b.KOLOR_DIFF) AS KOLOR_SUM,
                        SUM(b.MONO_DIFF + b.KOLOR_DIFF) AS LACZNIE_SUM,
                        CASE
                            WHEN COUNT(DISTINCT b.ID_MASZYNA) = 0 THEN NULL
                            ELSE SUM(b.MONO_DIFF + b.KOLOR_DIFF) * 1.0 / COUNT(DISTINCT b.ID_MASZYNA)
                        END AS SREDNIO_NA_URZADZENIE
                    FROM cpc_base b
                    WHERE b.MODEL <> ''
                    GROUP BY b.MODEL
                    ORDER BY LACZNIE_SUM DESC, b.MODEL
                """
                cursor.execute(sql, (start_key, end_key))
                columns, mapped_rows = self._fetch_cursor_rows(cursor, row_limit)
                return {
                    "intent": intent,
                    "criteria": period_criteria,
                    "columns": columns,
                    "rows": mapped_rows,
                    "row_count": len(mapped_rows),
                    "limited_to": row_limit,
                    "generated_sql": sql.strip(),
                }

            if intent == "device_monthly_print_by_serial":
                serial = (serial_number or "").strip()
                if not serial:
                    raise RuntimeError(
                        "Dla intent=device_monthly_print_by_serial wymagane jest `serial_number`."
                    )
                start_key, end_key, period_criteria = _resolve_year_month_window(
                    months_back=months_back
                )
                sql = """
                    WITH cpc_base AS (
                        SELECT
                            m.ID_MASZYNA,
                            TRIM(COALESCE(m.MODEL, '')) AS MODEL,
                            TRIM(COALESCE(m.SERIAL, '')) AS SERIAL,
                            c.ROK AS ROK,
                            COALESCE(NULLIF(c.MIESIAC, 0), 1) AS MIESIAC,
                            (
                                COALESCE(c.LICZNIK_MONO_END, c.LICZNIK_MONO_START, 0)
                                - COALESCE(c.LICZNIK_MONO_START, c.LICZNIK_MONO_END, 0)
                            ) AS MONO_DIFF,
                            (
                                COALESCE(c.LICZNIK_KOLOR_END, c.LICZNIK_KOLOR_START, 0)
                                - COALESCE(c.LICZNIK_KOLOR_START, c.LICZNIK_KOLOR_END, 0)
                            ) AS KOLOR_DIFF
                        FROM CPC c
                        JOIN MASZYNA m ON m.ID_MASZYNA = c.ID_MASZYNA
                        WHERE UPPER(COALESCE(m.SERIAL, '')) CONTAINING ?
                          AND ((c.ROK * 100) + COALESCE(NULLIF(c.MIESIAC, 0), 1)) BETWEEN ? AND ?
                    )
                    SELECT
                        b.ID_MASZYNA,
                        b.MODEL,
                        b.SERIAL,
                        b.ROK,
                        b.MIESIAC,
                        COUNT(*) AS REKORDY_CPC,
                        SUM(b.MONO_DIFF) AS MONO_SUM,
                        SUM(b.KOLOR_DIFF) AS KOLOR_SUM,
                        SUM(b.MONO_DIFF + b.KOLOR_DIFF) AS LACZNIE_SUM
                    FROM cpc_base b
                    GROUP BY b.ID_MASZYNA, b.MODEL, b.SERIAL, b.ROK, b.MIESIAC
                    ORDER BY b.ROK DESC, b.MIESIAC DESC, b.ID_MASZYNA DESC
                """
                cursor.execute(sql, (serial.upper(), start_key, end_key))
                effective_row_limit = min(
                    max(row_limit, months_back),
                    runtime_settings.firebird_row_limit,
                )
                columns, mapped_rows = self._fetch_cursor_rows(cursor, effective_row_limit)
                return {
                    "intent": intent,
                    "criteria": {"serial_number": serial, **period_criteria},
                    "columns": columns,
                    "rows": mapped_rows,
                    "row_count": len(mapped_rows),
                    "limited_to": effective_row_limit,
                    "generated_sql": sql.strip(),
                }

            if intent == "active_devices_on_contracts":
                count_sql = """
                    SELECT COUNT(*)
                    FROM MASZYNA m
                    LEFT JOIN UMOWACPC uc ON uc.ID_UMOWACPC = m.ID_UMOWACPC
                    WHERE COALESCE(m.ID_UMOWACPC, 0) <> 0
                      AND (
                          UPPER(TRIM(COALESCE(m.AKTYWNA, ''))) IN ('T', 'TAK', '1', 'Y', 'YES')
                          OR UPPER(TRIM(COALESCE(uc.AKTYWNA, ''))) IN ('T', 'TAK', '1', 'Y', 'YES')
                          OR m.UM_K IS NULL
                          OR m.UM_K >= CURRENT_DATE
                          OR uc.U_STOP IS NULL
                          OR uc.U_STOP >= CURRENT_DATE
                      )
                """
                cursor.execute(count_sql)
                count_row = cursor.fetchone()
                total_count = int(count_row[0]) if count_row and count_row[0] is not None else 0
                sql = """
                    SELECT
                        m.ID_MASZYNA,
                        m.ID_KLIENT,
                        TRIM(COALESCE(k.NAZWA, '')) AS KLIENT_NAZWA,
                        TRIM(COALESCE(m.MARKA, '')) AS MARKA,
                        TRIM(COALESCE(m.MODEL, '')) AS MODEL,
                        TRIM(COALESCE(m.SERIAL, '')) AS SERIAL,
                        TRIM(COALESCE(m.EWIDENCJA, '')) AS EWIDENCJA,
                        TRIM(COALESCE(m.AKTYWNA, '')) AS AKTYWNA_MASZYNA,
                        m.UM_P,
                        m.UM_K,
                        m.ID_UMOWACPC,
                        TRIM(COALESCE(uc.UMOWA, '')) AS UMOWA_CPC,
                        TRIM(COALESCE(uc.AKTYWNA, '')) AS AKTYWNA_UMOWA_CPC,
                        uc.U_START,
                        uc.U_STOP
                    FROM MASZYNA m
                    JOIN KLIENT k ON k.ID_KLIENT = m.ID_KLIENT
                    LEFT JOIN UMOWACPC uc ON uc.ID_UMOWACPC = m.ID_UMOWACPC
                    WHERE COALESCE(m.ID_UMOWACPC, 0) <> 0
                      AND (
                          UPPER(TRIM(COALESCE(m.AKTYWNA, ''))) IN ('T', 'TAK', '1', 'Y', 'YES')
                          OR UPPER(TRIM(COALESCE(uc.AKTYWNA, ''))) IN ('T', 'TAK', '1', 'Y', 'YES')
                          OR m.UM_K IS NULL
                          OR m.UM_K >= CURRENT_DATE
                          OR uc.U_STOP IS NULL
                          OR uc.U_STOP >= CURRENT_DATE
                      )
                    ORDER BY k.NAZWA, m.MODEL, m.SERIAL
                """
                cursor.execute(sql)
                columns, mapped_rows = self._fetch_cursor_rows(cursor, row_limit)
                return {
                    "intent": intent,
                    "criteria": {"active_only": True, "contracts_only": True},
                    "columns": columns,
                    "rows": mapped_rows,
                    "row_count": len(mapped_rows),
                    "total_count": total_count,
                    "limited_to": row_limit,
                    "generated_sql": sql.strip(),
                }

            if intent == "active_devices_on_contracts_count":
                sql = """
                    SELECT COUNT(*)
                    FROM MASZYNA m
                    LEFT JOIN UMOWACPC uc ON uc.ID_UMOWACPC = m.ID_UMOWACPC
                    WHERE COALESCE(m.ID_UMOWACPC, 0) <> 0
                      AND (
                          UPPER(TRIM(COALESCE(m.AKTYWNA, ''))) IN ('T', 'TAK', '1', 'Y', 'YES')
                          OR UPPER(TRIM(COALESCE(uc.AKTYWNA, ''))) IN ('T', 'TAK', '1', 'Y', 'YES')
                          OR m.UM_K IS NULL
                          OR m.UM_K >= CURRENT_DATE
                          OR uc.U_STOP IS NULL
                          OR uc.U_STOP >= CURRENT_DATE
                      )
                """
                cursor.execute(sql)
                count_row = cursor.fetchone()
                total_count = int(count_row[0]) if count_row and count_row[0] is not None else 0
                return {
                    "intent": intent,
                    "criteria": {"active_only": True, "contracts_only": True},
                    "columns": ["TOTAL_COUNT"],
                    "rows": [{"TOTAL_COUNT": total_count}],
                    "row_count": 1,
                    "total_count": total_count,
                    "limited_to": 1,
                    "generated_sql": sql.strip(),
                }

            raise RuntimeError(
                "Nieobsługiwany intent `firebird_business_read`. "
                "Dozwolone: devices_by_company, monthly_average_print_by_model, "
                "company_monthly_print_summary, top_models_by_volume, "
                "device_monthly_print_by_serial, active_devices_on_contracts, "
                "contract_settlement_period_explainer, "
                "active_devices_on_contracts_count."
            )
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:  # pragma: no cover
                    pass
            try:
                connection.close()
            except Exception:  # pragma: no cover
                pass

    def _run_sheets_read_sync(
        self,
        runtime_config: WorkflowSheetRuntimeConfig,
        worksheet_name: str | None,
        range_name: str | None,
        row_limit: int,
    ) -> dict[str, Any]:
        enabled, reason = workflow_sheet_sync_configured(runtime_config)
        if not enabled:
            raise RuntimeError(reason or "Google Sheets dla workflow nie jest skonfigurowany.")

        workbook, _ = _open_workbook(runtime_config, readonly=True)
        if worksheet_name:
            worksheet = workbook.worksheet(worksheet_name)
        else:
            worksheet = _resolve_devices_worksheet(workbook, runtime_config, strict=False)

        if range_name:
            values = worksheet.get(range_name)
        else:
            values = worksheet.get_all_values()

        if not values:
            return {
                "worksheet": worksheet.title,
                "range": range_name,
                "columns": [],
                "rows": [],
                "row_count": 0,
                "limited_to": row_limit,
            }

        headers = [str(item or "").strip() for item in values[0]]
        if not any(headers):
            headers = [f"col_{idx + 1}" for idx in range(len(values[0]))]

        rows_payload: list[dict[str, Any]] = []
        for row in values[1 : row_limit + 1]:
            padded = list(row) + [""] * max(0, len(headers) - len(row))
            item = {
                headers[idx] or f"col_{idx + 1}": _json_safe(padded[idx])
                for idx in range(len(headers))
            }
            rows_payload.append(item)

        return {
            "worksheet": worksheet.title,
            "range": range_name,
            "columns": headers,
            "rows": rows_payload,
            "row_count": len(rows_payload),
            "limited_to": row_limit,
        }

    async def execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> AssistantToolResult:
        """Uruchamia narzędzie z allowlisty."""
        result: AssistantToolResult
        if tool_name == "firebird_read":
            result = await self.firebird_read(
                sql=str(arguments.get("sql") or ""),
                row_limit=arguments.get("row_limit"),
            )
        elif tool_name == "firebird_business_read":
            result = await self.firebird_business_read(
                intent=str(arguments.get("intent") or ""),
                company_name=(
                    str(arguments.get("company_name")).strip()
                    if arguments.get("company_name") is not None
                    else None
                ),
                model_name=(
                    str(arguments.get("model_name")).strip()
                    if arguments.get("model_name") is not None
                    else None
                ),
                serial_number=(
                    str(arguments.get("serial_number")).strip()
                    if arguments.get("serial_number") is not None
                    else None
                ),
                months_back=arguments.get("months_back"),
                row_limit=arguments.get("row_limit"),
            )
        elif tool_name == "firebird_knowledge_read":
            result = await self.firebird_knowledge_read(
                table_name=(
                    str(arguments.get("table_name")).strip()
                    if arguments.get("table_name") is not None
                    else None
                ),
                topic=(
                    str(arguments.get("topic")).strip()
                    if arguments.get("topic") is not None
                    else None
                ),
                include_columns=_to_bool(arguments.get("include_columns"), True),
                row_limit=arguments.get("row_limit"),
            )
        elif tool_name == "sheets_read":
            result = await self.sheets_read(
                worksheet=(
                    str(arguments.get("worksheet")).strip() if arguments.get("worksheet") else None
                ),
                range_name=(
                    str(arguments.get("range_name")).strip()
                    if arguments.get("range_name")
                    else None
                ),
                row_limit=arguments.get("row_limit"),
            )
        elif tool_name == "imap_read":
            result = await self.imap_read(
                folder=(str(arguments.get("folder")).strip() if arguments.get("folder") else None),
                unread_only=_to_bool(arguments.get("unread_only"), True),
                since_days=arguments.get("since_days"),
                row_limit=arguments.get("row_limit"),
            )
        elif tool_name == "ctip_schema_read":
            result = await self.ctip_schema_read(
                table_name=(
                    str(arguments.get("table_name")).strip()
                    if arguments.get("table_name")
                    else None
                ),
                include_columns=_to_bool(arguments.get("include_columns"), True),
                include_relationships=_to_bool(arguments.get("include_relationships"), True),
                row_limit=arguments.get("row_limit"),
            )
        elif tool_name == "email_send_report":
            result = await self.email_send_report(
                recipient_email=str(arguments.get("recipient_email") or ""),
                subject=(
                    str(arguments.get("subject")).strip()
                    if arguments.get("subject") is not None
                    else None
                ),
                message_body=(
                    str(arguments.get("message_body")).strip()
                    if arguments.get("message_body") is not None
                    else None
                ),
                report_format=(
                    str(arguments.get("report_format")).strip()
                    if arguments.get("report_format") is not None
                    else None
                ),
                source_tool=(
                    str(arguments.get("source_tool")).strip()
                    if arguments.get("source_tool") is not None
                    else None
                ),
                report_title=(
                    str(arguments.get("report_title")).strip()
                    if arguments.get("report_title") is not None
                    else None
                ),
            )
        else:
            result = AssistantToolResult(
                tool_name=tool_name,
                status="blocked",
                payload={},
                row_count=None,
                generated_sql=None,
                error_message="Narzędzie nie znajduje się na liście dozwolonych.",
                duration_ms=0,
            )

        self._tool_history.append(result)
        return result


__all__ = [
    "AssistantDataTools",
    "AssistantToolResult",
    "AssistantToolRuntimeConfig",
    "AssistantSqlGuardError",
]
