"""Synchronizacja danych workflow z arkuszem Google dla procesu GRENKE."""

from __future__ import annotations

import re
import unicodedata
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services import firebird_ms_users
from app.services.settings_store import build_store

SHEET_DEFAULT_WORKSHEET = "Urzadzenia_magazyn"
SHEET_FALLBACK_WORKSHEETS = ("Urzadzenia",)
GOOGLE_SHEETS_NAMESPACE = "google_sheets"

_HEADER_LABELS = {
    "producer": "PRODUCENT",
    "model": "MODEL",
    "index": "INDEKS",
    "serial": "SERIAL",
    "status": "STATUS",
    "ms_id_magazyn_table": "MS_ID_MAGAZYN_TABLE",
    "reservation_grenke": "REZERWACJA GRENKE",
    "proforma_grenke": "FAKTURA PROFORMA GRENKE",
}

_HEADER_ALIASES = {
    "producer": {"producent", "marka"},
    "model": {"model", "nazwa", "urzadzenie"},
    "index": {"indeks", "ewidencja", "nr wewnetrzny", "nr wew", "index"},
    "serial": {"serial", "sn", "s n", "s/n", "nr seryjny"},
    "status": {"status", "status urzadzenia"},
    "notes": {"uwagi", "uwaga", "informacja"},
    "form_ctip": {"formularz ctip", "nr formularza", "numer formularza", "formularz"},
    "ctip_form_id": {"ctip_form_id", "ctip form id"},
    "ctip_workflow_case_id": {
        "ctip_workflow_case_id",
        "ctip workflow case id",
        "workflow case id",
    },
    "business_status_legacy": {
        "status handlowy legacy",
        "status handlowy",
    },
    "ms_id_magazyn_table": {
        "ms_id_magazyn_table",
        "ms id magazyn table",
        "id magazyn table",
        "id_magazyn_table",
    },
    "reservation_grenke": {"rezerwacja grenke"},
    "proforma_grenke": {
        "faktura proforma grenke",
        "proforma grenke",
        "nr proformy grenke",
    },
}

_REQUIRED_HEADER_KEYS = (
    "producer",
    "model",
    "index",
    "serial",
    "status",
    "ms_id_magazyn_table",
    "reservation_grenke",
    "proforma_grenke",
)

_PREFERRED_STATUS_HEADER_TOKENS = ("status rezerwacji",)

_WORKFLOW_BOOTSTRAP_HEADER_LAYOUT = [
    "PRODUCENT",
    "MODEL",
    "SERIAL",
    "EWIDENCJA",
    "STATUS",
    "LICZNIK B/W",
    "LICZNIK KOLOR",
    "CENA",
    "UWAGI",
    "REZERWACJA GRENKE",
    "Osoba obsługująca",
    "FORMULARZ CTIP",
    "FAKTURA PROFORMA GRENKE",
    "CTIP_FORM_ID",
    "CTIP_WORKFLOW_CASE_ID",
    "STATUS HANDLOWY (LEGACY)",
    "MS_ID_MAGAZYN_TABLE",
]

_OPTIONAL_WORKSHEET_HEADER_TOKENS = {
    "licznik b w",
    "licznik kolor",
    "cena",
    "uwagi",
    "osoba obslugujaca",
    "osoba obs ugujaca",
    "formularz ctip",
    "ctip form id",
    "ctip workflow case id",
    "status handlowy legacy",
}


@dataclass(slots=True)
class WorkflowSheetRuntimeConfig:
    """Runtime konfiguracja Google Sheets używana przez FLOW."""

    enabled: bool
    credentials_path: str
    spreadsheet_id: str
    workflow_devices_worksheet: str
    source: str = "env"


_settings_store = build_store(settings.admin_secret_key)
_workflow_sheet_runtime_config_var: ContextVar[WorkflowSheetRuntimeConfig | None] = ContextVar(
    "workflow_sheet_runtime_config",
    default=None,
)

WORKFLOW_RESERVATION_STATUS = "04. Rezerwacja GRENKE"
WORKFLOW_RESERVATION_NOTE = "Rezerwacja zalozona automatycznie przez CTIP."
WORKFLOW_RESERVED_ROW_COLOR = {"red": 0.98, "green": 0.89, "blue": 0.89}
WORKFLOW_DEFAULT_ROW_COLOR = {"red": 1.0, "green": 1.0, "blue": 1.0}


def _coerce_bool(value: str | bool | None, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return value.strip().lower() in {"1", "true", "t", "yes", "on"}


def normalize_workflow_sheet_spreadsheet_id(value: str | None) -> str:
    """Normalizuje identyfikator skoroszytu Google z URL lub surowego ID."""

    raw_value = str(value or "").strip()
    if not raw_value:
        return ""

    candidate = raw_value
    if candidate.startswith(("docs.google.com/", "drive.google.com/")):
        candidate = f"https://{candidate}"

    parsed = urlparse(candidate)
    if not parsed.scheme and not parsed.netloc:
        return raw_value

    match = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", parsed.path or "")
    if match:
        return match.group(1)

    query_params = parse_qs(parsed.query)
    for key in ("id", "key"):
        values = query_params.get(key)
        if values:
            normalized = str(values[0]).strip()
            if normalized:
                return normalized

    return raw_value


def _default_workflow_sheet_runtime_config() -> WorkflowSheetRuntimeConfig:
    return WorkflowSheetRuntimeConfig(
        enabled=True,
        credentials_path=(settings.google_application_credentials or "").strip(),
        spreadsheet_id=normalize_workflow_sheet_spreadsheet_id(
            settings.google_sheets_spreadsheet_id
        ),
        workflow_devices_worksheet=(
            settings.google_sheets_workflow_devices_sheet or SHEET_DEFAULT_WORKSHEET
        ).strip()
        or SHEET_DEFAULT_WORKSHEET,
        source="env",
    )


async def load_workflow_sheet_runtime_config(session: AsyncSession) -> WorkflowSheetRuntimeConfig:
    """Ładuje konfigurację Google Sheets dla FLOW z panelu admin z fallbackiem do env."""

    defaults = _default_workflow_sheet_runtime_config()
    stored = await _settings_store.get_namespace(session, GOOGLE_SHEETS_NAMESPACE)
    if not stored:
        return defaults

    return WorkflowSheetRuntimeConfig(
        enabled=_coerce_bool(stored.get("enabled"), True),
        credentials_path=(stored.get("credentials_path") or "").strip(),
        spreadsheet_id=normalize_workflow_sheet_spreadsheet_id(stored.get("spreadsheet_id")),
        workflow_devices_worksheet=(
            stored.get("workflow_devices_worksheet") or SHEET_DEFAULT_WORKSHEET
        ).strip()
        or SHEET_DEFAULT_WORKSHEET,
        source="admin",
    )


def _resolve_workflow_sheet_runtime_config() -> WorkflowSheetRuntimeConfig:
    return _workflow_sheet_runtime_config_var.get() or _default_workflow_sheet_runtime_config()


@contextmanager
def use_workflow_sheet_runtime_config(config: WorkflowSheetRuntimeConfig | None):
    """Aktywuje runtime config Google Sheets dla bieżącego kontekstu zadania/wątku."""

    if config is None:
        yield
        return
    token = _workflow_sheet_runtime_config_var.set(config)
    try:
        yield
    finally:
        _workflow_sheet_runtime_config_var.reset(token)


def workflow_sheet_sync_configured(
    config: WorkflowSheetRuntimeConfig | None = None,
) -> tuple[bool, str | None]:
    """Sprawdza, czy konfiguracja Google Sheets jest kompletna dla synchronizacji workflow."""

    active_config = config or _resolve_workflow_sheet_runtime_config()
    if not active_config.enabled:
        return False, "Synchronizacja arkusza jest wyłączona w panelu administratora."

    if not active_config.credentials_path:
        if active_config.source == "admin":
            return False, "Brak ścieżki do credentials Google Sheets."
        return False, "Brak GOOGLE_APPLICATION_CREDENTIALS."
    if not active_config.spreadsheet_id:
        if active_config.source == "admin":
            return False, "Brak spreadsheet_id Google Sheets."
        return False, "Brak GOOGLE_SHEETS_SPREADSHEET_ID."
    if not active_config.workflow_devices_worksheet:
        return False, "Brak nazwy zakładki urządzeń workflow."

    resolved = Path(active_config.credentials_path).expanduser()
    if not resolved.exists():
        return False, f"Brak pliku credentials Google: {resolved}"

    return True, None


def test_workflow_sheet_connection(
    config: WorkflowSheetRuntimeConfig | None = None,
) -> dict[str, Any]:
    """Sprawdza dostęp do skoroszytu i komplet wymaganych nagłówków FLOW."""

    active_config = config or _resolve_workflow_sheet_runtime_config()
    enabled, reason = workflow_sheet_sync_configured(active_config)
    if not enabled:
        return {
            "success": False,
            "message": reason or "Konfiguracja Google Sheets jest niepełna.",
            "service_account_email": None,
            "spreadsheet_title": None,
            "worksheet_found": False,
            "worksheet_title": None,
            "missing_headers": [],
        }

    try:
        workbook, service_account_email = _open_workbook(active_config, readonly=True)
        worksheet = _resolve_devices_worksheet(workbook, active_config, strict=True)
        values = worksheet.get_all_values()
        headers = list(values[0]) if values else []
        missing_headers = _missing_required_headers(headers)
    except Exception as exc:  # noqa: BLE001
        detail = str(exc).strip() or f"{type(exc).__name__} podczas testu Google Sheets."
        return {
            "success": False,
            "message": detail,
            "service_account_email": None,
            "spreadsheet_title": None,
            "worksheet_found": False,
            "worksheet_title": None,
            "missing_headers": [],
        }

    if missing_headers:
        return {
            "success": False,
            "message": (
                "Arkusz Google jest dostępny, ale brakuje wymaganych nagłówków FLOW: "
                + ", ".join(missing_headers)
            ),
            "service_account_email": service_account_email,
            "spreadsheet_title": workbook.title,
            "worksheet_found": True,
            "worksheet_title": worksheet.title,
            "missing_headers": missing_headers,
        }

    return {
        "success": True,
        "message": "Połączenie z arkuszem Google dla FLOW zakończone sukcesem.",
        "service_account_email": service_account_email,
        "spreadsheet_title": workbook.title,
        "worksheet_found": True,
        "worksheet_title": worksheet.title,
        "missing_headers": [],
    }


def bootstrap_workflow_sheet_headers(
    config: WorkflowSheetRuntimeConfig | None = None,
) -> dict[str, Any]:
    """Przygotowuje brakujące nagłówki FLOW w skonfigurowanej zakładce."""

    active_config = config or _resolve_workflow_sheet_runtime_config()
    enabled, reason = workflow_sheet_sync_configured(active_config)
    if not enabled:
        return {
            "success": False,
            "message": reason or "Konfiguracja Google Sheets jest niepełna.",
            "service_account_email": None,
            "spreadsheet_title": None,
            "worksheet_title": None,
            "added_headers": [],
            "existing_headers": [],
        }

    try:
        workbook, service_account_email = _open_workbook(active_config)
        worksheet = _resolve_devices_worksheet(workbook, active_config, strict=True)
        values = worksheet.get_all_values()
        headers_before = list(values[0]) if values else []
        if _looks_like_header_row(headers_before):
            header_index_before = _build_header_index(headers_before)
            existing_headers = [
                str(headers_before[header_index_before[key]]).strip()
                for key in _REQUIRED_HEADER_KEYS
                if key in header_index_before
                and 0 <= header_index_before[key] < len(headers_before)
            ]
        else:
            existing_headers = []
        _, _, _, added_headers = _prepare_headers(workbook, worksheet)
    except Exception as exc:  # noqa: BLE001
        detail = str(exc).strip() or f"{type(exc).__name__} podczas przygotowania nagłówków."
        return {
            "success": False,
            "message": detail,
            "service_account_email": None,
            "spreadsheet_title": None,
            "worksheet_title": None,
            "added_headers": [],
            "existing_headers": [],
        }

    if added_headers:
        message = "Przygotowano nagłówki FLOW. Dodano: " + ", ".join(added_headers) + "."
    else:
        message = "Nagłówki FLOW są już kompletne."

    return {
        "success": True,
        "message": message,
        "service_account_email": service_account_email,
        "spreadsheet_title": workbook.title,
        "worksheet_title": worksheet.title,
        "added_headers": added_headers,
        "existing_headers": existing_headers,
    }


def load_workflow_sheet_devices_lookup(
    config: WorkflowSheetRuntimeConfig | None = None,
) -> dict[str, Any]:
    """Zwraca mapę bieżących danych urządzeń z arkusza FLOW do wzbogacania listy workflow."""

    active_config = config or _resolve_workflow_sheet_runtime_config()
    enabled, reason = workflow_sheet_sync_configured(active_config)
    if not enabled:
        return {
            "enabled": False,
            "reason": reason,
            "worksheet_title": None,
            "by_source_key": {},
            "by_index": {},
        }

    workbook, _ = _open_workbook(active_config, readonly=True)
    worksheet = _resolve_devices_worksheet(workbook, active_config, strict=True)
    values = worksheet.get_all_values()
    if not values:
        return {
            "enabled": True,
            "reason": None,
            "worksheet_title": worksheet.title,
            "by_source_key": {},
            "by_index": {},
        }

    headers = list(values[0])
    header_index = _build_header_index(headers)
    by_source_key: dict[str, dict[str, str]] = {}
    by_index: dict[str, dict[str, str]] = {}

    for row_number, row in enumerate(values[1:], start=2):
        local_row = _ensure_row_width(list(row), len(headers))
        index_value = _row_value(local_row, header_index.get("index"))
        status_value = _row_value(local_row, header_index.get("status"))
        reservation_value = _row_value(local_row, header_index.get("reservation_grenke"))
        form_ctip_value = _row_value(local_row, header_index.get("form_ctip"))
        ctip_form_id_value = _row_value(local_row, header_index.get("ctip_form_id"))
        workflow_case_id_value = _row_value(local_row, header_index.get("ctip_workflow_case_id"))
        business_status_value = _row_value(local_row, header_index.get("business_status_legacy"))
        ms_id_value = _row_value(local_row, header_index.get("ms_id_magazyn_table"))
        entry = {
            "sheet_row": str(row_number),
            "status": status_value,
            "reservation_grenke": reservation_value,
            "form_ctip": form_ctip_value,
            "ctip_form_id": ctip_form_id_value,
            "ctip_workflow_case_id": workflow_case_id_value,
            "business_status_legacy": business_status_value,
            "ms_id_magazyn_table": ms_id_value,
            "index": index_value,
        }

        try:
            source_row = int(ms_id_value)
        except (TypeError, ValueError):
            source_row = 0
        if source_row > 0:
            by_source_key[f"firebird_magazyn_28:{source_row}"] = entry

        normalized_index = _normalize_device_key(index_value)
        if normalized_index and normalized_index not in by_index:
            by_index[normalized_index] = entry

    return {
        "enabled": True,
        "reason": None,
        "worksheet_title": worksheet.title,
        "by_source_key": by_source_key,
        "by_index": by_index,
    }


async def list_workflow_sheet_assignee_options(session: AsyncSession) -> list[dict[str, Any]]:
    """Zwraca opcje użytkowników MS do wyboru osoby rezerwującej w arkuszu."""

    users = await firebird_ms_users.list_firebird_ms_users(session)
    return [
        {
            "id": item.id,
            "login_user": item.login_user,
            "label": item.label,
        }
        for item in users
    ]


async def resolve_workflow_sheet_assignee(
    session: AsyncSession,
    assignee_id: int,
) -> dict[str, Any]:
    """Rozwiazuje i waliduje wskazanego użytkownika MS."""

    item = await firebird_ms_users.resolve_firebird_ms_user(session, assignee_id)
    return {
        "id": item.id,
        "login_user": item.login_user,
        "label": item.label,
    }


def sync_workflow_devices_to_sheet(
    *,
    devices: list[dict[str, Any]],
    assignee_label: str,
    proforma_number: str,
    form_request_id: int | None = None,
    workflow_case_id: int | None = None,
    business_status_label: str | None = None,
    status_value: str = WORKFLOW_RESERVATION_STATUS,
    note_value: str = WORKFLOW_RESERVATION_NOTE,
) -> dict[str, Any]:
    """Aktualizuje arkusz urządzeń po zapisie workflow i synchronizacji rezerwacji."""

    config = _resolve_workflow_sheet_runtime_config()
    enabled, reason = workflow_sheet_sync_configured(config)
    if not enabled:
        return {
            "enabled": False,
            "reason": reason,
            "worksheet_title": None,
            "synced_count": 0,
            "rows": [],
            "added_headers": [],
        }

    workbook, _ = _open_workbook(config)
    worksheet = _resolve_devices_worksheet(workbook, config)

    headers, header_index, values, added_headers = _prepare_headers(workbook, worksheet)
    data_rows = [list(row) for row in values[1:]] if len(values) > 1 else []
    should_write_reservation_status = _should_write_reservation_status(headers, header_index)

    updates: list[dict[str, Any]] = []
    row_results: list[dict[str, Any]] = []
    highlight_rows: list[int] = []
    next_row_number = len(data_rows) + 2
    normalized_assignee = _normalize_sheet_assignee_label(assignee_label)
    form_request_text = str(form_request_id).strip() if form_request_id is not None else ""
    workflow_case_text = str(workflow_case_id).strip() if workflow_case_id is not None else ""
    business_status_text = str(business_status_label or "").strip()

    for device in devices:
        row_number = _find_matching_row_number(data_rows, header_index, device)
        action = "updated"
        previous_status = str(device.get("sheet_previous_status") or "").strip()
        if row_number is None:
            action = "appended"
            row_values = [""] * len(headers)
            _fill_base_device_fields(row_values, header_index, device)
            if should_write_reservation_status:
                _set_row_value(row_values, header_index, "status", status_value)
            _set_row_value(row_values, header_index, "notes", note_value)
            _set_row_value(row_values, header_index, "reservation_grenke", normalized_assignee)
            _set_row_value(row_values, header_index, "form_ctip", form_request_text)
            _set_row_value(row_values, header_index, "proforma_grenke", proforma_number)
            _set_row_value(row_values, header_index, "ctip_form_id", form_request_text)
            _set_row_value(row_values, header_index, "ctip_workflow_case_id", workflow_case_text)
            _set_row_value(
                row_values,
                header_index,
                "business_status_legacy",
                business_status_text,
            )
            _set_row_value(
                row_values,
                header_index,
                "ms_id_magazyn_table",
                _coerce_source_row_text(device),
            )

            worksheet.append_row(row_values, value_input_option="USER_ENTERED")
            row_number = next_row_number
            next_row_number += 1
            data_rows.append(row_values)
        else:
            local_row = _ensure_row_width(data_rows[row_number - 2], len(headers))
            current_status = _row_value(local_row, header_index.get("status"))
            if (
                should_write_reservation_status
                and current_status
                and current_status != status_value
            ):
                previous_status = current_status
            _fill_base_device_fields(local_row, header_index, device, only_if_missing=True)
            if should_write_reservation_status:
                _queue_single_cell_update(
                    updates,
                    row_number,
                    header_index,
                    "status",
                    status_value,
                )
            _queue_single_cell_update(
                updates,
                row_number,
                header_index,
                "notes",
                note_value,
            )
            _queue_single_cell_update(
                updates,
                row_number,
                header_index,
                "reservation_grenke",
                normalized_assignee,
            )
            _queue_single_cell_update(
                updates,
                row_number,
                header_index,
                "form_ctip",
                form_request_text,
            )
            _queue_single_cell_update(
                updates,
                row_number,
                header_index,
                "proforma_grenke",
                proforma_number,
            )
            _queue_single_cell_update(
                updates,
                row_number,
                header_index,
                "ctip_form_id",
                form_request_text,
            )
            _queue_single_cell_update(
                updates,
                row_number,
                header_index,
                "ctip_workflow_case_id",
                workflow_case_text,
            )
            _queue_single_cell_update(
                updates,
                row_number,
                header_index,
                "business_status_legacy",
                business_status_text,
            )
            _queue_single_cell_update(
                updates,
                row_number,
                header_index,
                "ms_id_magazyn_table",
                _coerce_source_row_text(device),
            )
            data_rows[row_number - 2] = local_row

        highlight_rows.append(row_number)
        row_results.append(
            {
                "source_row": _coerce_int(device.get("source_row") or device.get("row")),
                "sheet_row": row_number,
                "action": action,
                "previous_status": previous_status,
            }
        )

    if updates:
        worksheet.batch_update(updates, value_input_option="USER_ENTERED")

    _set_row_background_color(
        workbook,
        worksheet,
        row_numbers=highlight_rows,
        header_len=len(headers),
        background_color=WORKFLOW_RESERVED_ROW_COLOR,
    )
    _hide_helper_column(workbook, worksheet, header_index)

    return {
        "enabled": True,
        "reason": None,
        "worksheet_title": worksheet.title,
        "synced_count": len(row_results),
        "rows": row_results,
        "added_headers": added_headers,
    }


def release_workflow_devices_from_sheet(
    *,
    devices: list[dict[str, Any]],
    default_status: str = "DOSTEPNE",
) -> dict[str, Any]:
    """Czyści rezerwację urządzeń workflow w arkuszu Google."""

    config = _resolve_workflow_sheet_runtime_config()
    enabled, reason = workflow_sheet_sync_configured(config)
    if not enabled:
        return {
            "enabled": False,
            "reason": reason,
            "worksheet_title": None,
            "released_count": 0,
            "rows": [],
            "added_headers": [],
        }

    workbook, _ = _open_workbook(config)
    worksheet = _resolve_devices_worksheet(workbook, config)

    headers, header_index, values, added_headers = _prepare_headers(workbook, worksheet)
    data_rows = [list(row) for row in values[1:]] if len(values) > 1 else []
    should_write_reservation_status = _should_write_reservation_status(headers, header_index)
    release_status_value = _default_release_status_value(headers, header_index, default_status)

    updates: list[dict[str, Any]] = []
    row_results: list[dict[str, Any]] = []
    cleared_rows: list[int] = []
    for device in devices:
        row_number = _find_matching_row_number(data_rows, header_index, device)
        if row_number is None:
            continue

        current_row = _ensure_row_width(data_rows[row_number - 2], len(headers))
        current_status = _row_value(current_row, header_index.get("status"))
        restore_status = (
            str(device.get("sheet_previous_status") or "").strip() or release_status_value
        )
        if should_write_reservation_status and _normalize_header_token(
            current_status
        ) == _normalize_header_token(WORKFLOW_RESERVATION_STATUS):
            _queue_single_cell_update(
                updates,
                row_number,
                header_index,
                "status",
                restore_status,
            )
        _queue_single_cell_update(updates, row_number, header_index, "notes", "")
        _queue_single_cell_update(updates, row_number, header_index, "reservation_grenke", "")
        _queue_single_cell_update(updates, row_number, header_index, "form_ctip", "")
        _queue_single_cell_update(updates, row_number, header_index, "proforma_grenke", "")
        _queue_single_cell_update(updates, row_number, header_index, "ctip_form_id", "")
        _queue_single_cell_update(updates, row_number, header_index, "ctip_workflow_case_id", "")
        _queue_single_cell_update(updates, row_number, header_index, "business_status_legacy", "")
        cleared_rows.append(row_number)

        row_results.append(
            {
                "source_row": _coerce_int(device.get("source_row") or device.get("row")),
                "sheet_row": row_number,
                "action": "released",
            }
        )

    if updates:
        worksheet.batch_update(updates, value_input_option="USER_ENTERED")

    _set_row_background_color(
        workbook,
        worksheet,
        row_numbers=cleared_rows,
        header_len=len(headers),
        background_color=WORKFLOW_DEFAULT_ROW_COLOR,
    )
    _hide_helper_column(workbook, worksheet, header_index)

    return {
        "enabled": True,
        "reason": None,
        "worksheet_title": worksheet.title,
        "released_count": len(row_results),
        "rows": row_results,
        "added_headers": added_headers,
    }


def clear_workflow_proforma_from_sheet(*, devices: list[dict[str, Any]]) -> dict[str, Any]:
    """Czyści numer proformy z arkusza bez zwalniania rezerwacji urządzeń."""

    config = _resolve_workflow_sheet_runtime_config()
    enabled, reason = workflow_sheet_sync_configured(config)
    if not enabled:
        return {
            "enabled": False,
            "reason": reason,
            "worksheet_title": None,
            "cleared_count": 0,
            "rows": [],
            "added_headers": [],
        }

    workbook, _ = _open_workbook(config)
    worksheet = _resolve_devices_worksheet(workbook, config)

    headers, header_index, values, added_headers = _prepare_headers(workbook, worksheet)
    data_rows = [list(row) for row in values[1:]] if len(values) > 1 else []

    updates: list[dict[str, Any]] = []
    row_results: list[dict[str, Any]] = []
    for device in devices:
        row_number = _find_matching_row_number(data_rows, header_index, device)
        if row_number is None:
            continue
        _queue_single_cell_update(updates, row_number, header_index, "proforma_grenke", "")
        row_results.append(
            {
                "source_row": _coerce_int(device.get("source_row") or device.get("row")),
                "sheet_row": row_number,
                "action": "proforma_cleared",
            }
        )

    if updates:
        worksheet.batch_update(updates, value_input_option="USER_ENTERED")

    _hide_helper_column(workbook, worksheet, header_index)

    return {
        "enabled": True,
        "reason": None,
        "worksheet_title": worksheet.title,
        "cleared_count": len(row_results),
        "rows": row_results,
        "added_headers": added_headers,
    }


def _open_workbook(
    config: WorkflowSheetRuntimeConfig | None = None,
    *,
    readonly: bool = False,
):
    active_config = config or _resolve_workflow_sheet_runtime_config()
    if not active_config.credentials_path or not active_config.spreadsheet_id:
        raise RuntimeError("Brak konfiguracji Google Sheets (credentials/spreadsheet_id).")

    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Brak biblioteki Google Sheets: {exc}") from exc

    scopes = (
        [
            "https://www.googleapis.com/auth/spreadsheets.readonly",
            "https://www.googleapis.com/auth/drive.readonly",
        ]
        if readonly
        else [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
    )
    credentials = Credentials.from_service_account_file(
        active_config.credentials_path,
        scopes=scopes,
    )
    client = gspread.authorize(credentials)
    return client.open_by_key(active_config.spreadsheet_id), credentials.service_account_email


def _resolve_devices_worksheet(
    workbook,
    config: WorkflowSheetRuntimeConfig | None = None,
    *,
    strict: bool = False,
):
    active_config = config or _resolve_workflow_sheet_runtime_config()
    requested = active_config.workflow_devices_worksheet or SHEET_DEFAULT_WORKSHEET
    normalized_requested = _normalize_header_token(requested)

    worksheets = workbook.worksheets()
    for worksheet in worksheets:
        if _normalize_header_token(worksheet.title) == normalized_requested:
            return worksheet

    if not strict:
        for fallback in SHEET_FALLBACK_WORKSHEETS:
            normalized_fallback = _normalize_header_token(fallback)
            for worksheet in worksheets:
                if _normalize_header_token(worksheet.title) == normalized_fallback:
                    return worksheet

    available_titles = ", ".join(sorted(worksheet.title for worksheet in worksheets))
    raise RuntimeError(
        "Nie znaleziono arkusza urządzeń workflow. "
        f"Szukano: '{requested}'. Dostępne: {available_titles or '(brak)'}"
    )


def _prepare_headers(workbook, worksheet):
    values = worksheet.get_all_values()
    added_headers: list[str] = []
    if not values:
        headers = _build_bootstrap_header_row(0)
        header_index = _build_header_index(headers)
        added_headers = [label for label in headers if str(label).strip()]
        worksheet.update(range_name="A1", values=[headers], value_input_option="USER_ENTERED")
        values = [headers]
    elif _looks_like_header_row(values[0]):
        headers = list(values[0])
        header_index = _build_header_index(headers)
    else:
        width = max(len(row) for row in values)
        headers = _build_bootstrap_header_row(width)
        header_index = _build_header_index(headers)
        added_headers = [label for label in headers if str(label).strip()]
        cleaned_first_row = _ensure_row_width(
            _strip_appended_header_suffix(values[0]), len(headers)
        )

        worksheet.insert_row(headers, index=1, value_input_option="USER_ENTERED")
        if cleaned_first_row != _ensure_row_width(values[0], len(headers)):
            worksheet.update(
                range_name=f"A2:{_column_letter(len(headers))}2",
                values=[cleaned_first_row],
                value_input_option="USER_ENTERED",
            )
        values = [headers, cleaned_first_row] + [
            _ensure_row_width(list(row), len(headers)) for row in values[1:]
        ]

    for key in _REQUIRED_HEADER_KEYS:
        if key in header_index:
            continue
        label = _HEADER_LABELS[key]
        headers.append(label)
        header_index[key] = len(headers) - 1
        added_headers.append(label)

    if values and headers != list(values[0]):
        worksheet.update(range_name="A1", values=[headers], value_input_option="USER_ENTERED")
        values[0] = headers

    values = _drop_duplicate_header_row(worksheet, headers, values)
    _format_header_row(worksheet, headers)
    _hide_helper_column(workbook, worksheet, header_index)
    return headers, header_index, values, added_headers


def _missing_required_headers(headers: list[str]) -> list[str]:
    header_index = _build_header_index(headers)
    return [_HEADER_LABELS[key] for key in _REQUIRED_HEADER_KEYS if key not in header_index]


def _build_bootstrap_header_row(existing_width: int) -> list[str]:
    width = max(existing_width, len(_WORKFLOW_BOOTSTRAP_HEADER_LAYOUT))
    headers = list(_WORKFLOW_BOOTSTRAP_HEADER_LAYOUT)
    if len(headers) < width:
        headers.extend([""] * (width - len(headers)))
    return headers


def _drop_duplicate_header_row(
    worksheet, headers: list[str], values: list[list[str]]
) -> list[list[str]]:
    if len(values) < 2:
        return values

    second_row = _ensure_row_width(list(values[1]), len(headers))
    if not _looks_like_header_row(second_row):
        return values

    matching_non_empty = 0
    for idx, cell in enumerate(second_row[: len(headers)]):
        cell_text = str(cell or "").strip()
        if not cell_text:
            continue
        if _normalize_header_token(cell_text) != _normalize_header_token(headers[idx]):
            return values
        matching_non_empty += 1

    if matching_non_empty < 5:
        return values

    try:
        worksheet.delete_rows(2)
    except Exception:  # noqa: BLE001
        return values
    return [values[0]] + values[2:]


def _format_header_row(worksheet, headers: list[str]) -> None:
    if not headers:
        return

    header_len = len(headers)
    technical_start = max(13, header_len - 4)
    column_widths = [
        130,
        180,
        150,
        130,
        130,
        110,
        120,
        90,
        190,
        170,
        160,
        140,
        220,
        120,
        150,
        170,
        150,
    ]
    if len(column_widths) < header_len:
        column_widths.extend([140] * (header_len - len(column_widths)))

    try:
        sheet_id = int(worksheet.id)
        requests: list[dict[str, Any]] = [
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": sheet_id,
                        "gridProperties": {"frozenRowCount": 1},
                    },
                    "fields": "gridProperties.frozenRowCount",
                }
            },
            {
                "setBasicFilter": {
                    "filter": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 0,
                            "startColumnIndex": 0,
                            "endColumnIndex": header_len,
                        }
                    }
                }
            },
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "endRowIndex": 1,
                        "startColumnIndex": 0,
                        "endColumnIndex": technical_start,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": {"red": 0.11, "green": 0.25, "blue": 0.47},
                            "textFormat": {
                                "foregroundColor": {"red": 1, "green": 1, "blue": 1},
                                "bold": True,
                                "fontSize": 10,
                            },
                            "horizontalAlignment": "CENTER",
                            "verticalAlignment": "MIDDLE",
                            "wrapStrategy": "WRAP",
                        }
                    },
                    "fields": (
                        "userEnteredFormat(backgroundColor,textFormat,"
                        "horizontalAlignment,verticalAlignment,wrapStrategy)"
                    ),
                }
            },
        ]
        if technical_start < header_len:
            requests.append(
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 0,
                            "endRowIndex": 1,
                            "startColumnIndex": technical_start,
                            "endColumnIndex": header_len,
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "backgroundColor": {"red": 0.29, "green": 0.33, "blue": 0.38},
                                "textFormat": {
                                    "foregroundColor": {"red": 1, "green": 1, "blue": 1},
                                    "bold": True,
                                    "fontSize": 10,
                                },
                                "horizontalAlignment": "CENTER",
                                "verticalAlignment": "MIDDLE",
                                "wrapStrategy": "WRAP",
                            }
                        },
                        "fields": (
                            "userEnteredFormat(backgroundColor,textFormat,"
                            "horizontalAlignment,verticalAlignment,wrapStrategy)"
                        ),
                    }
                }
            )
        requests.append(
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "ROWS",
                        "startIndex": 0,
                        "endIndex": 1,
                    },
                    "properties": {"pixelSize": 38},
                    "fields": "pixelSize",
                }
            }
        )
        for index, width in enumerate(column_widths[:header_len]):
            requests.append(
                {
                    "updateDimensionProperties": {
                        "range": {
                            "sheetId": sheet_id,
                            "dimension": "COLUMNS",
                            "startIndex": index,
                            "endIndex": index + 1,
                        },
                        "properties": {"pixelSize": width},
                        "fields": "pixelSize",
                    }
                }
            )
        worksheet.spreadsheet.batch_update({"requests": requests})
    except Exception:  # noqa: BLE001
        # Formatowanie jest dodatkiem UX - nie moze blokowac synchronizacji.
        return


def _looks_like_header_row(row: list[str]) -> bool:
    tokens = [_normalize_header_token(cell) for cell in row if str(cell or "").strip()]
    if not tokens:
        return False
    return all(_is_known_header_token(token) for token in tokens)


def _is_known_header_token(token: str) -> bool:
    if not token:
        return True
    if token in _PREFERRED_STATUS_HEADER_TOKENS:
        return True
    if token in _OPTIONAL_WORKSHEET_HEADER_TOKENS:
        return True
    return any(token in aliases for aliases in _HEADER_ALIASES.values())


def _strip_appended_header_suffix(row: list[str]) -> list[str]:
    if _looks_like_header_row(row):
        return list(row)

    cleaned = list(row)
    start_idx: int | None = None
    recognized_count = 0
    for idx in range(len(cleaned) - 1, -1, -1):
        token = _normalize_header_token(cleaned[idx])
        if not token:
            continue
        if _is_known_header_token(token):
            start_idx = idx
            recognized_count += 1
            continue
        break

    if start_idx is None or recognized_count < 3:
        return cleaned

    for idx in range(start_idx, len(cleaned)):
        token = _normalize_header_token(cleaned[idx])
        if _is_known_header_token(token):
            cleaned[idx] = ""
    return cleaned


def _build_header_index(headers: list[str]) -> dict[str, int]:
    output: dict[str, int] = {}
    for idx, header in enumerate(headers):
        token = _normalize_header_token(header)
        if token in _PREFERRED_STATUS_HEADER_TOKENS:
            output["status"] = idx
            break
    for idx, header in enumerate(headers):
        canonical = _header_to_canonical_key(header)
        if canonical and canonical not in output:
            output[canonical] = idx
    return output


def _default_release_status_value(
    headers: list[str],
    header_index: dict[str, int],
    fallback: str,
) -> str:
    status_idx = header_index.get("status")
    if status_idx is None or status_idx < 0 or status_idx >= len(headers):
        return fallback
    token = _normalize_header_token(headers[status_idx])
    if token in _PREFERRED_STATUS_HEADER_TOKENS:
        return "brak rezerwacji"
    return fallback


def _should_write_reservation_status(
    headers: list[str],
    header_index: dict[str, int],
) -> bool:
    status_idx = header_index.get("status")
    if status_idx is None or status_idx < 0 or status_idx >= len(headers):
        return False
    return _normalize_header_token(headers[status_idx]) in _PREFERRED_STATUS_HEADER_TOKENS


def _header_to_canonical_key(header: str | None) -> str | None:
    token = _normalize_header_token(header)
    if not token:
        return None
    for key, aliases in _HEADER_ALIASES.items():
        if token in aliases:
            return key
    return None


def _normalize_header_token(value: str | None) -> str:
    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return text.strip()


def _normalize_device_key(value: str | None) -> str:
    text = str(value or "").strip().upper()
    return re.sub(r"[^A-Z0-9]", "", text)


def _normalize_sheet_assignee_label(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return re.sub(r"\s*\([^)]*\)\s*$", "", text).strip()


def _coerce_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_source_row_text(device: dict[str, Any]) -> str:
    value = device.get("source_row")
    if value in (None, ""):
        value = device.get("row")
    int_value = _coerce_int(value)
    if int_value is None:
        return ""
    return str(int_value)


def _find_matching_row_number(
    data_rows: list[list[str]],
    header_index: dict[str, int],
    device: dict[str, Any],
) -> int | None:
    sheet_row = _coerce_int(device.get("sheet_row"))
    if sheet_row is not None and 2 <= sheet_row <= (len(data_rows) + 1):
        return sheet_row

    source_row_text = _coerce_source_row_text(device)
    if source_row_text:
        source_idx = header_index.get("ms_id_magazyn_table")
        if source_idx is not None:
            for row_no, row in enumerate(data_rows, start=2):
                if _row_value(row, source_idx) == source_row_text:
                    return row_no

    index_key = _normalize_device_key(device.get("index") or device.get("ewidencja"))
    if index_key:
        index_idx = header_index.get("index")
        if index_idx is not None:
            for row_no, row in enumerate(data_rows, start=2):
                if _normalize_device_key(_row_value(row, index_idx)) == index_key:
                    return row_no

    serial_key = _normalize_device_key(device.get("serial"))
    if serial_key:
        serial_idx = header_index.get("serial")
        if serial_idx is not None:
            for row_no, row in enumerate(data_rows, start=2):
                if _normalize_device_key(_row_value(row, serial_idx)) == serial_key:
                    return row_no

    return None


def _fill_base_device_fields(
    row_values: list[str],
    header_index: dict[str, int],
    device: dict[str, Any],
    *,
    only_if_missing: bool = False,
) -> None:
    data_map = {
        "producer": str(device.get("producer") or "").strip(),
        "model": str(device.get("model") or device.get("name") or "").strip(),
        "index": str(device.get("index") or device.get("ewidencja") or "").strip(),
        "serial": str(device.get("serial") or "").strip(),
    }

    for key, value in data_map.items():
        if not value:
            continue
        idx = header_index.get(key)
        if idx is None:
            continue
        if only_if_missing and _row_value(row_values, idx):
            continue
        row_values[idx] = value


def _set_row_value(
    row_values: list[str],
    header_index: dict[str, int],
    key: str,
    value: Any,
) -> None:
    idx = header_index.get(key)
    if idx is None:
        return
    if idx >= len(row_values):
        row_values.extend([""] * (idx - len(row_values) + 1))
    row_values[idx] = str(value or "").strip()


def _queue_single_cell_update(
    updates: list[dict[str, Any]],
    row_number: int,
    header_index: dict[str, int],
    key: str,
    value: Any,
) -> None:
    idx = header_index.get(key)
    if idx is None:
        return
    column_letter = _column_letter(idx + 1)
    updates.append(
        {
            "range": f"{column_letter}{row_number}",
            "values": [[str(value or "").strip()]],
        }
    )


def _hide_helper_column(workbook, worksheet, header_index: dict[str, int]) -> None:
    helper_idx = header_index.get("ms_id_magazyn_table")
    if helper_idx is None:
        return
    try:
        workbook.batch_update(
            {
                "requests": [
                    {
                        "updateDimensionProperties": {
                            "range": {
                                "sheetId": worksheet.id,
                                "dimension": "COLUMNS",
                                "startIndex": helper_idx,
                                "endIndex": helper_idx + 1,
                            },
                            "properties": {"hiddenByUser": True},
                            "fields": "hiddenByUser",
                        }
                    }
                ]
            }
        )
    except Exception:  # noqa: BLE001
        # Ukrywanie kolumny jest dodatkiem UX - blad nie powinien blokowac synchronizacji.
        return


def _set_row_background_color(
    workbook,
    worksheet,
    *,
    row_numbers: list[int],
    header_len: int,
    background_color: dict[str, float],
) -> None:
    if not row_numbers or header_len <= 0:
        return

    requests = []
    for row_number in sorted({row for row in row_numbers if row and row > 1}):
        requests.append(
            {
                "repeatCell": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startRowIndex": row_number - 1,
                        "endRowIndex": row_number,
                        "startColumnIndex": 0,
                        "endColumnIndex": header_len,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": background_color,
                        }
                    },
                    "fields": "userEnteredFormat.backgroundColor",
                }
            }
        )

    if not requests:
        return

    try:
        workbook.batch_update({"requests": requests})
    except Exception:  # noqa: BLE001
        # Formatowanie wiersza jest dodatkiem UX - blad nie powinien blokowac synchronizacji.
        return


def _column_letter(index: int) -> str:
    letters = ""
    current = index
    while current > 0:
        current, rem = divmod(current - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _row_value(row: list[str], idx: int | None) -> str:
    if idx is None or idx < 0 or idx >= len(row):
        return ""
    return str(row[idx] or "").strip()


def _ensure_row_width(row: list[str], target: int) -> list[str]:
    if len(row) >= target:
        return row
    return row + ([""] * (target - len(row)))


__all__ = [
    "bootstrap_workflow_sheet_headers",
    "clear_workflow_proforma_from_sheet",
    "list_workflow_sheet_assignee_options",
    "load_workflow_sheet_devices_lookup",
    "load_workflow_sheet_runtime_config",
    "normalize_workflow_sheet_spreadsheet_id",
    "release_workflow_devices_from_sheet",
    "resolve_workflow_sheet_assignee",
    "sync_workflow_devices_to_sheet",
    "test_workflow_sheet_connection",
    "WorkflowSheetRuntimeConfig",
    "use_workflow_sheet_runtime_config",
    "workflow_sheet_sync_configured",
]
