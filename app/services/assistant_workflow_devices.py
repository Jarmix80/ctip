"""Audyt urządzeń workflow dla CTIP AI Asystenta."""

from __future__ import annotations

import asyncio
import re
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.contracts_dashboard import FirebirdRuntimeConfig, load_firebird_runtime_config
from app.services.workflow_sheet_sync import (
    WorkflowSheetRuntimeConfig,
    load_workflow_sheet_runtime_config,
    workflow_sheet_sync_configured,
)

SOURCE_WORKSHEET_TITLE = "Urzadzenia_magazyn"
CHAT_WORKSHEET_TITLE = "urzadzenia_chat"
SPREADSHEET_TITLE = "Zerowki_prod"
DEFAULT_WAREHOUSE_ID = 28
WORKFLOW_DEVICES_STAGE_REQUEST_TYPE = "workflow_devices_chat_sheet_stage"
CHAT_NOTE_MARKER = "[CTIP/CHAT]"

_HEADER_ALIASES = {
    "producer": {"producent", "marka"},
    "model": {"model", "nazwa", "urzadzenie"},
    "serial": {"serial", "sn", "s n", "s/n", "nr seryjny"},
    "index": {"indeks", "ewidencja", "nr wewnetrzny", "nr wew", "index"},
    "status": {"status", "status urzadzenia"},
    "notes": {"uwagi", "uwaga", "informacja"},
    "reservation_grenke": {"rezerwacja grenke"},
    "proforma_grenke": {"faktura proforma grenke", "proforma grenke"},
    "ms_id_magazyn_table": {
        "ms_id_magazyn_table",
        "ms id magazyn table",
        "id magazyn table",
        "id_magazyn_table",
    },
}


@dataclass(slots=True, frozen=True)
class WorkflowDevicesAuditResult:
    """Wynik audytu urządzeń dla asystenta."""

    payload: dict[str, Any]
    duration_ms: int


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _normalize_prompt(value: str) -> str:
    return _strip_accents(value).lower()


def is_workflow_devices_audit_prompt(prompt: str) -> bool:
    """Rozpoznaje pytania o porównanie urządzeń Firebird z arkuszem."""

    normalized = _normalize_prompt(prompt)
    if any(token in normalized for token in ("bez porown", "nie porown")):
        return False
    has_device = any(token in normalized for token in ("urzadz", "urzadzen", "urzadzenia"))
    has_sheet = any(token in normalized for token in ("arkusz", "sheets", "google", "zerowki"))
    has_firebird = any(
        token in normalized for token in ("firebird", "menadzer", "menadze", "fb", "ms")
    )
    has_audit = any(
        token in normalized
        for token in ("sprawdz", "porown", "roznic", "rozbiezn", "audyt", "stan", "dopisz")
    )
    return has_device and has_sheet and has_firebird and has_audit


def _normalize_header_token(value: str | None) -> str:
    text_value = _strip_accents(str(value or "").strip().lower())
    return re.sub(r"[^0-9a-z]+", " ", text_value).strip()


def _normalize_match_key(value: str | None) -> str:
    return "".join(str(value or "").strip().lower().split())


def _normalize_firebird_token(value: str | None) -> str:
    return "".join(ch for ch in str(value or "").upper() if ch not in "/- ")


def _text(value: Any, *, limit: int | None = None) -> str:
    raw = str(value or "").strip()
    return raw[:limit] if limit is not None else raw


def _column_letter(col_idx_1based: int) -> str:
    value = col_idx_1based
    output = ""
    while value > 0:
        value, rem = divmod(value - 1, 26)
        output = chr(65 + rem) + output
    return output


def _header_index(headers: list[str]) -> dict[str, int]:
    mapped = {_normalize_header_token(label): idx for idx, label in enumerate(headers)}
    result: dict[str, int] = {}
    for key, aliases in _HEADER_ALIASES.items():
        for alias in aliases:
            idx = mapped.get(_normalize_header_token(alias))
            if idx is not None:
                result[key] = idx
                break
    return result


def _row_value(row: list[str], idx: int | None) -> str:
    if idx is None or idx < 0 or idx >= len(row):
        return ""
    return _text(row[idx])


def _ensure_width(row: list[Any], width: int) -> list[str]:
    values = [_text(value) for value in row]
    if len(values) < width:
        values.extend([""] * (width - len(values)))
    return values[:width]


def _set_row_value(row: list[str], header_index: dict[str, int], key: str, value: Any) -> None:
    idx = header_index.get(key)
    if idx is None or idx >= len(row):
        return
    row[idx] = _text(value)


def _append_note(row: list[str], header_index: dict[str, int], note: str) -> None:
    idx = header_index.get("notes")
    if idx is None or idx >= len(row):
        return
    current = _text(row[idx])
    row[idx] = f"{current}\n{note}".strip() if current else note


def parse_serial_and_model(value: str | None) -> tuple[str, str]:
    """Wyciąga numer seryjny z tekstu modelu/nazwy urządzenia."""

    raw = _text(value)
    if not raw:
        return "", ""
    match = re.search(r"(?i)\bS\s*/?\s*N\s*:\s*([A-Za-z0-9\-_\/]+)", raw)
    if not match:
        return "", raw
    serial = match.group(1).strip()
    model = (raw[: match.start()] + raw[match.end() :]).strip(" ,;-/")
    return serial, re.sub(r"\s+", " ", model).strip()


def derive_brand_model(value: str | None) -> tuple[str, str]:
    """Wyznacza producenta i model z tekstu Firebird, gdy pola MARKA/MODEL są puste."""

    raw = re.sub(r"\s+", " ", _text(value)).strip()
    if not raw:
        return "", ""
    upper = raw.upper()
    if upper.startswith("KONICA MINOLTA "):
        return "Konica Minolta", raw[len("Konica Minolta ") :].strip()
    parts = raw.split(" ", 1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1].strip()


def parse_sheet_rows(
    values: list[list[str]],
) -> tuple[list[str], dict[str, int], dict[int, dict[str, Any]]]:
    """Parsuje wiersze arkusza urządzeń do ujednoliconego formatu."""

    if not values:
        return [], {}, {}
    headers = [_text(item) for item in values[0]]
    idx = _header_index(headers)
    rows: dict[int, dict[str, Any]] = {}
    width = len(headers)
    for sheet_row_no, row in enumerate(values[1:], start=2):
        row_ext = _ensure_width(list(row), width)
        ms_raw = _row_value(row_ext, idx.get("ms_id_magazyn_table"))
        try:
            ms_id = int(ms_raw)
        except ValueError:
            ms_id = 0
        rows[sheet_row_no] = {
            "sheet_row": sheet_row_no,
            "values": row_ext,
            "ms_id": ms_id if ms_id > 0 else None,
            "ms_raw": ms_raw,
            "index": _row_value(row_ext, idx.get("index")),
            "model": _row_value(row_ext, idx.get("model")),
            "producer": _row_value(row_ext, idx.get("producer")),
            "serial": _row_value(row_ext, idx.get("serial")),
            "status": _row_value(row_ext, idx.get("status")),
            "notes": _row_value(row_ext, idx.get("notes")),
        }
    return headers, idx, rows


def _build_fill_stage_row(
    *,
    headers: list[str],
    header_index: dict[str, int],
    source_row: dict[str, Any],
    ms_id: int,
) -> dict[str, Any]:
    values = _ensure_width(list(source_row.get("values") or []), len(headers))
    _set_row_value(values, header_index, "ms_id_magazyn_table", ms_id)
    _append_note(
        values,
        header_index,
        f"{CHAT_NOTE_MARKER} UZUPELNIJ_MS_ID; wiersz docelowy {source_row['sheet_row']}; MS_ID {ms_id}",
    )
    return {
        "action": "fill_ms_id",
        "source_sheet_row": int(source_row["sheet_row"]),
        "ms_id_magazyn_table": int(ms_id),
        "index": source_row.get("index") or "",
        "target_values": values,
    }


def _build_append_stage_row(
    *,
    headers: list[str],
    header_index: dict[str, int],
    device: dict[str, Any],
) -> dict[str, Any]:
    values = [""] * len(headers)
    parsed_serial, parsed_model_text = parse_serial_and_model(device.get("name"))
    producer = _text(device.get("producer"), limit=80)
    model = _text(device.get("model"), limit=160)
    if not producer or not model:
        derived_producer, derived_model = derive_brand_model(
            model or parsed_model_text or device.get("name")
        )
        producer = producer or derived_producer
        model = model or derived_model
    serial = _text(device.get("serial"), limit=100)
    if serial.upper() in {"", "NIE", "BRAK"}:
        serial = parsed_serial

    _set_row_value(values, header_index, "producer", producer)
    _set_row_value(values, header_index, "model", model or parsed_model_text or device.get("name"))
    _set_row_value(values, header_index, "serial", serial)
    _set_row_value(values, header_index, "index", device.get("index"))
    _set_row_value(values, header_index, "status", "01. Przed zerówką")
    _set_row_value(values, header_index, "reservation_grenke", "")
    _set_row_value(values, header_index, "proforma_grenke", "")
    _set_row_value(values, header_index, "ms_id_magazyn_table", device.get("ms_id_magazyn_table"))
    _append_note(
        values,
        header_index,
        f"{CHAT_NOTE_MARKER} DOPISZ_WIERSZ; MS_ID {device.get('ms_id_magazyn_table')}",
    )
    return {
        "action": "append_row",
        "source_sheet_row": None,
        "ms_id_magazyn_table": int(device["ms_id_magazyn_table"]),
        "index": device.get("index") or "",
        "target_values": values,
    }


def build_workflow_devices_audit_payload(
    *,
    sheet_values: list[list[str]],
    firebird_devices: dict[int, dict[str, Any]],
    spreadsheet_id: str,
    spreadsheet_title: str,
    source_worksheet_title: str = SOURCE_WORKSHEET_TITLE,
    chat_worksheet_title: str = CHAT_WORKSHEET_TITLE,
    warehouse_id: int = DEFAULT_WAREHOUSE_ID,
) -> dict[str, Any]:
    """Buduje raport rozbieżności i paczkę stagingu dla `urzadzenia_chat`."""

    headers, idx, sheet_rows = parse_sheet_rows(sheet_values)
    if not headers:
        raise RuntimeError("Arkusz urządzeń jest pusty.")
    if "ms_id_magazyn_table" not in idx:
        raise RuntimeError("Arkusz nie zawiera kolumny MS_ID_MAGAZYN_TABLE.")

    by_ms_sheet: dict[int, dict[str, Any]] = {}
    by_index_without_ms: dict[str, list[dict[str, Any]]] = defaultdict(list)
    without_ms_rows: list[dict[str, Any]] = []
    for row in sheet_rows.values():
        ms_id = row.get("ms_id")
        if ms_id:
            by_ms_sheet[int(ms_id)] = row
            continue
        if any(_text(value) for value in row.get("values") or []):
            without_ms_rows.append(row)
            index_key = _normalize_match_key(row.get("index"))
            if index_key:
                by_index_without_ms[index_key].append(row)

    fb_ids = set(firebird_devices)
    sheet_ids = set(by_ms_sheet)
    only_fb = sorted(fb_ids - sheet_ids)
    only_sheet = sorted(sheet_ids - fb_ids)

    match_rows = [
        {
            "sheet_row": int(row["sheet_row"]),
            "sheet_index": row.get("index") or "",
            "candidate_ms_id": int(ms_id),
        }
        for row in without_ms_rows
        for ms_id, device in firebird_devices.items()
        if _normalize_match_key(row.get("index"))
        and _normalize_match_key(row.get("index")) == _normalize_match_key(device.get("index"))
    ]

    stage_rows: list[dict[str, Any]] = []
    used_sheet_rows: set[int] = set()
    for ms_id in only_fb:
        device = firebird_devices[ms_id]
        index_key = _normalize_match_key(device.get("index"))
        candidates = [
            row
            for row in by_index_without_ms.get(index_key, [])
            if int(row["sheet_row"]) not in used_sheet_rows
        ]
        if candidates:
            source_row = candidates[0]
            used_sheet_rows.add(int(source_row["sheet_row"]))
            stage_rows.append(
                _build_fill_stage_row(
                    headers=headers,
                    header_index=idx,
                    source_row=source_row,
                    ms_id=ms_id,
                )
            )
        else:
            stage_rows.append(
                _build_append_stage_row(headers=headers, header_index=idx, device=device)
            )

    action_counts = Counter(str(row.get("action") or "") for row in stage_rows)
    generated_at = datetime.now(UTC).isoformat()
    only_in_sheet_examples = [
        {
            "ms_id_magazyn_table": int(ms_id),
            "sheet_row": int(by_ms_sheet[ms_id]["sheet_row"]),
            "index": by_ms_sheet[ms_id].get("index") or "",
            "model": by_ms_sheet[ms_id].get("model") or "",
            "status": by_ms_sheet[ms_id].get("status") or "",
        }
        for ms_id in only_sheet
    ]

    return {
        "type": "workflow_devices_audit",
        "generated_at_utc": generated_at,
        "spreadsheet": {
            "id": spreadsheet_id,
            "title": spreadsheet_title,
            "worksheet": source_worksheet_title,
            "chat_worksheet": chat_worksheet_title,
            "rows_total": max(0, len(sheet_values) - 1),
            "headers": headers,
        },
        "firebird": {
            "warehouse_id": warehouse_id,
            "rows_available": len(firebird_devices),
        },
        "summary": {
            "only_in_firebird_count": len(only_fb),
            "only_in_sheet_count": len(only_sheet),
            "sheet_rows_without_ms_id_count": len(without_ms_rows),
            "rows_without_ms_matching_index_count": len(match_rows),
            "stage_rows_count": len(stage_rows),
            "stage_fill_ms_id_count": int(action_counts.get("fill_ms_id", 0)),
            "stage_append_count": int(action_counts.get("append_row", 0)),
        },
        "discrepancies": {
            "only_in_firebird": [firebird_devices[ms_id] for ms_id in only_fb],
            "only_in_sheet": only_in_sheet_examples,
            "sheet_rows_without_ms_id": [
                {
                    "sheet_row": int(row["sheet_row"]),
                    "index": row.get("index") or "",
                    "producer": row.get("producer") or "",
                    "model": row.get("model") or "",
                    "status": row.get("status") or "",
                }
                for row in without_ms_rows
            ],
            "rows_without_ms_matching_index_in_firebird": match_rows,
        },
        "stage": {
            "type": WORKFLOW_DEVICES_STAGE_REQUEST_TYPE,
            "source_worksheet": source_worksheet_title,
            "target_worksheet": chat_worksheet_title,
            "headers": headers,
            "rows": stage_rows,
            "row_count": len(stage_rows),
            "summary": {
                "fill_ms_id_count": int(action_counts.get("fill_ms_id", 0)),
                "append_count": int(action_counts.get("append_row", 0)),
            },
        },
    }


def _open_firebird_connection(config: FirebirdRuntimeConfig):
    import firebirdsql  # type: ignore[import-not-found]

    connect_kwargs: dict[str, Any] = {
        "port": config.port,
        "user": config.user,
        "password": config.password,
        "charset": config.charset,
    }
    if config.role:
        connect_kwargs["role"] = config.role
    if config.mode == "network":
        return firebirdsql.connect(
            host=config.host,
            database=config.database,
            **connect_kwargs,
        )

    db_path = Path(config.local_copy_path)
    if not db_path.is_absolute():
        db_path = _repo_root() / db_path
    if not db_path.exists():
        raise FileNotFoundError(f"Brak lokalnej kopii Firebird: {db_path}")
    return firebirdsql.connect(host="127.0.0.1", database=str(db_path), **connect_kwargs)


def _load_firebird_available_devices(
    config: FirebirdRuntimeConfig,
    *,
    warehouse_id: int,
) -> dict[int, dict[str, Any]]:
    connection = _open_firebird_connection(config)
    cursor = None
    try:
        cursor = connection.cursor()
        try:
            cursor.execute("SET TRANSACTION READ ONLY")
        except Exception:  # noqa: BLE001
            pass
        cursor.execute(
            """
            SELECT
                ID_MAGAZYN_TABLE,
                INDEKS,
                NAZWA,
                MARKA,
                MODEL,
                ILOSC,
                IL_REZ,
                SERIAL,
                ID_MODEL
            FROM MAGAZYN
            WHERE COALESCE(ID_MAGAZYN, 0) = ?
              AND COALESCE(ILOSC, 0) > 0
            ORDER BY ID_MAGAZYN_TABLE DESC
            """,
            (warehouse_id,),
        )
        rows = cursor.fetchall()
    finally:
        if cursor is not None:
            cursor.close()
        connection.close()

    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        ms_id = int(row[0]) if row[0] is not None else 0
        if ms_id <= 0:
            continue
        total_qty = float(row[5] or 0)
        reserved_qty = float(row[6] or 0)
        available_qty = total_qty - reserved_qty
        if available_qty <= 0:
            continue
        result[ms_id] = {
            "ms_id_magazyn_table": ms_id,
            "index": _text(row[1], limit=100),
            "name": _text(row[2], limit=250),
            "producer": _text(row[3], limit=80),
            "model": _text(row[4], limit=160),
            "serial": _text(row[7], limit=100),
            "id_model": int(row[8]) if row[8] is not None else None,
            "available_qty": available_qty,
            "reserved_qty": reserved_qty,
            "warehouse_qty": total_qty,
        }
    return result


def _authorize_gspread(config: WorkflowSheetRuntimeConfig, *, readonly: bool):
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
    credentials = Credentials.from_service_account_file(config.credentials_path, scopes=scopes)
    return gspread.authorize(credentials)


def _open_workbook(config: WorkflowSheetRuntimeConfig, *, readonly: bool):
    client = _authorize_gspread(config, readonly=readonly)
    first_error: Exception | None = None
    if config.spreadsheet_id:
        try:
            return client.open_by_key(config.spreadsheet_id)
        except Exception as exc:  # noqa: BLE001
            first_error = exc
    try:
        return client.open(SPREADSHEET_TITLE)
    except Exception as exc:  # noqa: BLE001
        if first_error is not None:
            raise RuntimeError(
                f"Nie udało się otworzyć skoroszytu po ID ani po nazwie {SPREADSHEET_TITLE}: {first_error}"
            ) from exc
        raise


def _run_workflow_devices_audit_sync(
    *,
    firebird_config: FirebirdRuntimeConfig,
    sheet_config: WorkflowSheetRuntimeConfig,
    warehouse_id: int,
) -> dict[str, Any]:
    enabled, reason = workflow_sheet_sync_configured(sheet_config)
    if not enabled:
        raise RuntimeError(reason or "Konfiguracja Google Sheets jest niepełna.")
    workbook = _open_workbook(sheet_config, readonly=True)
    source_worksheet = workbook.worksheet(SOURCE_WORKSHEET_TITLE)
    sheet_values = source_worksheet.get_all_values()
    firebird_devices = _load_firebird_available_devices(
        firebird_config,
        warehouse_id=warehouse_id,
    )
    return build_workflow_devices_audit_payload(
        sheet_values=sheet_values,
        firebird_devices=firebird_devices,
        spreadsheet_id=workbook.id,
        spreadsheet_title=workbook.title,
        source_worksheet_title=source_worksheet.title,
        chat_worksheet_title=CHAT_WORKSHEET_TITLE,
        warehouse_id=warehouse_id,
    )


async def build_workflow_devices_audit_result(
    session: AsyncSession,
    *,
    warehouse_id: int = DEFAULT_WAREHOUSE_ID,
    timeout_seconds: int = 60,
) -> WorkflowDevicesAuditResult:
    """Wykonuje audyt urządzeń Firebird vs Google Sheets dla asystenta."""

    firebird_config = await load_firebird_runtime_config(session)
    sheet_config = await load_workflow_sheet_runtime_config(session)
    started = time.monotonic()
    payload = await asyncio.wait_for(
        asyncio.to_thread(
            _run_workflow_devices_audit_sync,
            firebird_config=firebird_config,
            sheet_config=sheet_config,
            warehouse_id=warehouse_id,
        ),
        timeout=timeout_seconds,
    )
    return WorkflowDevicesAuditResult(
        payload=payload,
        duration_ms=int((time.monotonic() - started) * 1000),
    )


def render_workflow_devices_audit_answer(payload: dict[str, Any]) -> str:
    """Renderuje zwięzłą odpowiedź tekstową dla czatu."""

    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    spreadsheet = payload.get("spreadsheet") if isinstance(payload.get("spreadsheet"), dict) else {}
    firebird = payload.get("firebird") if isinstance(payload.get("firebird"), dict) else {}
    discrepancies = (
        payload.get("discrepancies") if isinstance(payload.get("discrepancies"), dict) else {}
    )
    only_fb = discrepancies.get("only_in_firebird") if isinstance(discrepancies, dict) else []
    only_sheet = discrepancies.get("only_in_sheet") if isinstance(discrepancies, dict) else []
    matches = (
        discrepancies.get("rows_without_ms_matching_index_in_firebird")
        if isinstance(discrepancies, dict)
        else []
    )

    lines = [
        "Raport porównania urządzeń Firebird vs Google Sheets.",
        "",
        f"Skoroszyt: {spreadsheet.get('title', SPREADSHEET_TITLE)}, zakładka: {spreadsheet.get('worksheet', SOURCE_WORKSHEET_TITLE)}.",
        f"Firebird: magazyn {firebird.get('warehouse_id', DEFAULT_WAREHOUSE_ID)}, dostępne urządzenia: {firebird.get('rows_available', 0)}.",
        "",
        f"Tylko w Firebird: {summary.get('only_in_firebird_count', 0)}.",
        f"Tylko w arkuszu: {summary.get('only_in_sheet_count', 0)}.",
        f"Wiersze bez MS_ID_MAGAZYN_TABLE: {summary.get('sheet_rows_without_ms_id_count', 0)}.",
        f"Pasujące po indeksie do Firebird: {summary.get('rows_without_ms_matching_index_count', 0)}.",
        "",
        "Propozycja do zakładki urzadzenia_chat:",
        f"- uzupełnienie MS_ID: {summary.get('stage_fill_ms_id_count', 0)} wierszy,",
        f"- dopisanie nowych wierszy: {summary.get('stage_append_count', 0)} wierszy,",
        f"- razem do stagingu: {summary.get('stage_rows_count', 0)} wierszy.",
    ]

    if only_fb:
        lines.extend(["", "Przykłady tylko w Firebird:"])
        for item in only_fb[:10]:
            lines.append(
                f"- MS_ID {item.get('ms_id_magazyn_table')}: {item.get('index')} | {item.get('name')}"
            )
    if only_sheet:
        lines.extend(["", "Przykłady tylko w arkuszu:"])
        for item in only_sheet[:10]:
            lines.append(
                f"- wiersz {item.get('sheet_row')}, MS_ID {item.get('ms_id_magazyn_table')}: {item.get('index')} | {item.get('model')}"
            )
    if matches:
        lines.extend(["", "Wiersze bez MS_ID, które pasują po indeksie:"])
        for item in matches[:12]:
            lines.append(
                f"- wiersz {item.get('sheet_row')}: {item.get('sheet_index')} -> MS_ID {item.get('candidate_ms_id')}"
            )

    if int(summary.get("stage_rows_count") or 0) > 0:
        lines.extend(
            [
                "",
                "Możesz kliknąć `Zapisz do urzadzenia_chat`. Chat wyczyści zakładkę roboczą i wpisze tam świeżą paczkę do ręcznego przeniesienia. Docelowy arkusz Urzadzenia_magazyn nie zostanie zmieniony.",
            ]
        )
    else:
        lines.extend(["", "Nie ma wierszy do przygotowania w zakładce urzadzenia_chat."])
    return "\n".join(lines)


def build_workflow_devices_pending_action(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Buduje payload wniosku wykonywalnego dla stagingu w `urzadzenia_chat`."""

    stage = payload.get("stage") if isinstance(payload.get("stage"), dict) else {}
    if int(stage.get("row_count") or 0) <= 0:
        return None
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    spreadsheet = payload.get("spreadsheet") if isinstance(payload.get("spreadsheet"), dict) else {}
    return {
        "type": WORKFLOW_DEVICES_STAGE_REQUEST_TYPE,
        "label": "Zapisz do urzadzenia_chat",
        "request_text": "Zapisz wynik audytu urządzeń do zakładki roboczej urzadzenia_chat.",
        "justification": (
            "Staging danych dla operatora: docelowy arkusz Urzadzenia_magazyn pozostaje bez zmian."
        ),
        "summary": summary,
        "spreadsheet": spreadsheet,
        "audit_generated_at_utc": payload.get("generated_at_utc"),
        "stage": stage,
    }


def _execute_stage_write_sync(
    *,
    sheet_config: WorkflowSheetRuntimeConfig,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if payload.get("type") != WORKFLOW_DEVICES_STAGE_REQUEST_TYPE:
        raise RuntimeError("Nieobsługiwany typ akcji stagingu urządzeń.")
    stage = payload.get("stage") if isinstance(payload.get("stage"), dict) else {}
    headers = stage.get("headers") if isinstance(stage.get("headers"), list) else []
    rows_meta = stage.get("rows") if isinstance(stage.get("rows"), list) else []
    if not headers:
        raise RuntimeError("Brak nagłówków stagingu urządzeń.")

    workbook = _open_workbook(sheet_config, readonly=False)
    worksheet = workbook.worksheet(CHAT_WORKSHEET_TITLE)
    row_values = [
        _ensure_width(list(item.get("target_values") or []), len(headers))
        for item in rows_meta
        if isinstance(item, dict)
    ]
    target_rows = max(len(row_values) + 1, 1)
    target_cols = max(len(headers), worksheet.col_count)
    if worksheet.row_count < target_rows or worksheet.col_count < len(headers):
        worksheet.resize(rows=max(worksheet.row_count, target_rows), cols=target_cols)

    clear_end_col = _column_letter(max(target_cols, len(headers)))
    clear_end_row = max(worksheet.row_count, target_rows)
    if clear_end_row >= 2:
        worksheet.batch_clear([f"A2:{clear_end_col}{clear_end_row}"])
    worksheet.update(range_name="A1", values=[headers], value_input_option="USER_ENTERED")
    if row_values:
        end_col = _column_letter(len(headers))
        end_row = len(row_values) + 1
        worksheet.update(
            range_name=f"A2:{end_col}{end_row}",
            values=row_values,
            value_input_option="USER_ENTERED",
        )

    action_counts = Counter(
        str(item.get("action") or "") for item in rows_meta if isinstance(item, dict)
    )
    return {
        "spreadsheet_id": workbook.id,
        "spreadsheet_title": workbook.title,
        "worksheet_title": worksheet.title,
        "written_rows": len(row_values),
        "cleared_from_row": 2,
        "headers_count": len(headers),
        "fill_ms_id_count": int(action_counts.get("fill_ms_id", 0)),
        "append_count": int(action_counts.get("append_row", 0)),
        "executed_at_utc": datetime.now(UTC).isoformat(),
    }


async def execute_workflow_devices_chat_sheet_stage(
    session: AsyncSession,
    *,
    payload: dict[str, Any],
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    """Czyści i zapisuje paczkę urządzeń do zakładki `urzadzenia_chat`."""

    sheet_config = await load_workflow_sheet_runtime_config(session)
    enabled, reason = workflow_sheet_sync_configured(sheet_config)
    if not enabled:
        raise RuntimeError(reason or "Konfiguracja Google Sheets jest niepełna.")
    return await asyncio.wait_for(
        asyncio.to_thread(_execute_stage_write_sync, sheet_config=sheet_config, payload=payload),
        timeout=timeout_seconds,
    )


__all__ = [
    "CHAT_WORKSHEET_TITLE",
    "DEFAULT_WAREHOUSE_ID",
    "SOURCE_WORKSHEET_TITLE",
    "SPREADSHEET_TITLE",
    "WORKFLOW_DEVICES_STAGE_REQUEST_TYPE",
    "WorkflowDevicesAuditResult",
    "build_workflow_devices_audit_payload",
    "build_workflow_devices_audit_result",
    "build_workflow_devices_pending_action",
    "derive_brand_model",
    "execute_workflow_devices_chat_sheet_stage",
    "is_workflow_devices_audit_prompt",
    "parse_serial_and_model",
    "render_workflow_devices_audit_answer",
]
