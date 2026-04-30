"""Narzędzie operacyjne do audytu i synchronizacji urządzeń FLOW na produkcji.

Skrypt obsługuje scenariusze, które były wykonywane ręcznie:
1) audyt Firebird (MAGAZYN) vs arkusz Google `Urzadzenia_magazyn`,
2) uzupełnienie `MS_ID_MAGAZYN_TABLE` oraz dopisanie brakujących wierszy,
3) uzupełnienie `MASZYNA` po stronie Firebird dla zaktualizowanych pozycji,
4) przeniesienie numeru seryjnego z kolumny `MODEL` (fragment po `S/N:`) do `SERIAL`,
5) dopisywanie adnotacji w `UWAGI CODEX` bez nadpisywania treści.

Wszystkie operacje zapisują raport JSON do katalogu `inbox/`.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import firebirdsql
import gspread
from dotenv import dotenv_values

DEFAULT_SPREADSHEET_ID = "13P8RVLKrfhz26HqTTz4ZsjaVqaCMq7a8vqiTOLnKRT0"
DEFAULT_WORKSHEET_TITLE = "Urzadzenia_magazyn"
DEFAULT_REPORT_DIR = "inbox"
DEFAULT_WAREHOUSE_ID = 28
DEFAULT_WAREHOUSE_CLIENT_ID = 656
NOTES_MARKER = "[CTIP/CODEX]"


class ScriptError(RuntimeError):
    """Błąd kontrolowany dla czytelnego komunikatu CLI."""


def now_utc() -> datetime:
    return datetime.now(UTC)


def timestamp_slug() -> str:
    return now_utc().strftime("%Y%m%d_%H%M%S")


def normalize_key(value: str | None) -> str:
    return "".join(str(value or "").strip().lower().split())


def normalize_firebird_token(value: str | None) -> str:
    return "".join(ch for ch in str(value or "").upper() if ch not in "/- ")


def col_letter(col_idx_1based: int) -> str:
    value = col_idx_1based
    output = ""
    while value > 0:
        value, rem = divmod(value - 1, 26)
        output = chr(65 + rem) + output
    return output


def text(value: Any, *, limit: int | None = None) -> str:
    raw = str(value or "").strip()
    return raw[:limit] if limit is not None else raw


def load_env_map(env_file: Path) -> dict[str, str]:
    if not env_file.exists():
        raise ScriptError(f"Brak pliku środowiskowego: {env_file}")
    env = {k: str(v) for k, v in dotenv_values(env_file).items() if v is not None}
    return env


def build_report_path(report_dir: Path, prefix: str) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    return report_dir / f"{prefix}_{timestamp_slug()}.json"


def save_report(report_dir: Path, prefix: str, payload: dict[str, Any]) -> Path:
    output = build_report_path(report_dir, prefix)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def resolve_credentials_path(env: dict[str, str]) -> Path:
    credentials_path = Path(text(env.get("GOOGLE_APPLICATION_CREDENTIALS")))
    if not credentials_path.exists():
        raise ScriptError(
            "Brak pliku GOOGLE_APPLICATION_CREDENTIALS w lokalnym środowisku. "
            "Uzupełnij .env i uruchom ponownie."
        )
    return credentials_path


def open_worksheet(
    env: dict[str, str],
    *,
    spreadsheet_id: str,
    worksheet_title: str,
):
    credentials_path = resolve_credentials_path(env)
    client = gspread.service_account(filename=str(credentials_path))
    workbook = client.open_by_key(spreadsheet_id)
    worksheet = workbook.worksheet(worksheet_title)
    return workbook, worksheet


def open_firebird(env: dict[str, str]):
    return firebirdsql.connect(
        host=text(env.get("FB_HOST") or "192.168.0.8"),
        port=int(text(env.get("FB_PORT") or "3050")),
        database=text(env.get("FB_DATABASE")),
        user=text(env.get("FB_USER") or "SYSDBA"),
        password=text(env.get("FB_PASSWORD") or "masterkey"),
        charset=text(env.get("FB_CHARSET") or "WIN1250"),
    )


@dataclass(slots=True)
class HeaderIndex:
    producer: int | None
    model: int | None
    serial: int | None
    index: int | None
    status: int | None
    notes: int | None
    codex_notes: int | None
    reservation_grenke: int | None
    proforma_grenke: int | None
    ms_id_magazyn_table: int | None


def header_index(headers: list[str]) -> HeaderIndex:
    mapped = {normalize_key(label): idx for idx, label in enumerate(headers)}

    def first(*aliases: str) -> int | None:
        for alias in aliases:
            idx = mapped.get(normalize_key(alias))
            if idx is not None:
                return idx
        return None

    return HeaderIndex(
        producer=first("PRODUCENT", "MARKA"),
        model=first("MODEL", "NAZWA"),
        serial=first("SERIAL", "S/N", "SN"),
        index=first("INDEKS", "EWIDENCJA", "INDEX"),
        status=first("STATUS"),
        notes=first("UWAGI", "UWAGA"),
        codex_notes=first("UWAGI CODEX", "UWAGI_Codex", "UWAGI KODEX", "UWAGI CODEKS"),
        reservation_grenke=first("REZERWACJA GRENKE"),
        proforma_grenke=first("FAKTURA PROFORMA GRENKE", "PROFORMA GRENKE"),
        ms_id_magazyn_table=first("MS_ID_MAGAZYN_TABLE", "ID_MAGAZYN_TABLE"),
    )


def parse_sheet_rows(
    values: list[list[str]],
) -> tuple[list[str], HeaderIndex, dict[int, dict[str, Any]]]:
    if not values:
        return [], HeaderIndex(None, None, None, None, None, None, None, None, None, None), {}

    headers = [text(col) for col in values[0]]
    idx = header_index(headers)
    rows: dict[int, dict[str, Any]] = {}
    for sheet_row_no, row in enumerate(values[1:], start=2):
        row_ext = list(row) + [""] * max(0, len(headers) - len(row))
        ms_raw = ""
        if idx.ms_id_magazyn_table is not None and idx.ms_id_magazyn_table < len(row_ext):
            ms_raw = text(row_ext[idx.ms_id_magazyn_table])
        try:
            ms_id = int(ms_raw)
        except ValueError:
            ms_id = 0
        rows[sheet_row_no] = {
            "sheet_row": sheet_row_no,
            "values": row_ext,
            "ms_id": ms_id if ms_id > 0 else None,
            "ms_raw": ms_raw,
            "index": (
                text(row_ext[idx.index])
                if idx.index is not None and idx.index < len(row_ext)
                else ""
            ),
            "model": (
                text(row_ext[idx.model])
                if idx.model is not None and idx.model < len(row_ext)
                else ""
            ),
            "producer": (
                text(row_ext[idx.producer])
                if idx.producer is not None and idx.producer < len(row_ext)
                else ""
            ),
            "serial": (
                text(row_ext[idx.serial])
                if idx.serial is not None and idx.serial < len(row_ext)
                else ""
            ),
            "status": (
                text(row_ext[idx.status])
                if idx.status is not None and idx.status < len(row_ext)
                else ""
            ),
            "notes": (
                text(row_ext[idx.notes])
                if idx.notes is not None and idx.notes < len(row_ext)
                else ""
            ),
            "codex_notes": (
                text(row_ext[idx.codex_notes])
                if idx.codex_notes is not None and idx.codex_notes < len(row_ext)
                else ""
            ),
        }
    return headers, idx, rows


def firebird_available_devices(connection, *, warehouse_id: int) -> dict[int, dict[str, Any]]:
    cursor = connection.cursor()
    try:
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
                CENA_NETTO,
                CENA_BRUTTO,
                VAT_STAWKA,
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
        cursor.close()

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
            "index": text(row[1], limit=100),
            "name": text(row[2], limit=250),
            "producer": text(row[3], limit=50),
            "model": text(row[4], limit=50),
            "serial": text(row[10], limit=100),
            "id_model": int(row[11]) if row[11] is not None else None,
            "available_qty": available_qty,
            "reserved_qty": reserved_qty,
            "warehouse_qty": total_qty,
        }
    return result


def report_audit(
    env: dict[str, str],
    *,
    spreadsheet_id: str,
    worksheet_title: str,
    warehouse_id: int,
    report_dir: Path,
) -> Path:
    workbook, worksheet = open_worksheet(
        env, spreadsheet_id=spreadsheet_id, worksheet_title=worksheet_title
    )
    values = worksheet.get_all_values()
    headers, idx, sheet_rows = parse_sheet_rows(values)
    if idx.ms_id_magazyn_table is None:
        raise ScriptError("Arkusz nie zawiera kolumny MS_ID_MAGAZYN_TABLE.")

    by_ms_sheet: dict[int, dict[str, Any]] = {}
    by_index_without_ms: dict[str, list[dict[str, Any]]] = defaultdict(list)
    without_ms_rows: list[dict[str, Any]] = []
    for row in sheet_rows.values():
        ms_id = row["ms_id"]
        if ms_id:
            by_ms_sheet[ms_id] = row
        else:
            if any(text(value) for value in row["values"]):
                without_ms_rows.append(row)
                by_index_without_ms[normalize_key(row["index"])].append(row)

    connection = open_firebird(env)
    try:
        by_ms_fb = firebird_available_devices(connection, warehouse_id=warehouse_id)
    finally:
        connection.close()

    fb_ids = set(by_ms_fb)
    sheet_ids = set(by_ms_sheet)
    only_fb = sorted(fb_ids - sheet_ids)
    only_sheet = sorted(sheet_ids - fb_ids)

    report = {
        "generated_at_utc": now_utc().isoformat(),
        "spreadsheet": {
            "id": spreadsheet_id,
            "title": workbook.title,
            "worksheet": worksheet.title,
            "rows_total": max(0, len(values) - 1),
            "headers": headers,
        },
        "firebird": {
            "warehouse_id": warehouse_id,
            "rows_available": len(by_ms_fb),
        },
        "discrepancies": {
            "only_in_firebird_count": len(only_fb),
            "only_in_firebird_examples": [by_ms_fb[item] for item in only_fb[:200]],
            "only_in_sheet_count": len(only_sheet),
            "only_in_sheet_examples": [
                {
                    "ms_id_magazyn_table": item,
                    "sheet_row": by_ms_sheet[item]["sheet_row"],
                    "index": by_ms_sheet[item]["index"],
                    "model": by_ms_sheet[item]["model"],
                }
                for item in only_sheet[:200]
            ],
            "sheet_rows_without_ms_id_count": len(without_ms_rows),
            "sheet_rows_without_ms_id_examples": [
                {
                    "sheet_row": item["sheet_row"],
                    "index": item["index"],
                    "model": item["model"],
                    "producer": item["producer"],
                    "status": item["status"],
                }
                for item in without_ms_rows[:200]
            ],
            "rows_without_ms_matching_index_in_firebird": [
                {
                    "sheet_row": item["sheet_row"],
                    "sheet_index": item["index"],
                    "candidate_ms_id": by_ms_fb[candidate]["ms_id_magazyn_table"],
                }
                for item in without_ms_rows
                for candidate in by_ms_fb
                if normalize_key(item["index"]) == normalize_key(by_ms_fb[candidate]["index"])
            ],
        },
    }

    output = save_report(report_dir, "raport_urzadzenia_prod_audit", report)
    return output


def sync_sheet(
    env: dict[str, str],
    *,
    spreadsheet_id: str,
    worksheet_title: str,
    warehouse_id: int,
    report_dir: Path,
    write: bool,
) -> Path:
    workbook, worksheet = open_worksheet(
        env, spreadsheet_id=spreadsheet_id, worksheet_title=worksheet_title
    )
    values = worksheet.get_all_values()
    headers, idx, sheet_rows = parse_sheet_rows(values)
    if not headers:
        raise ScriptError("Arkusz jest pusty.")
    if idx.ms_id_magazyn_table is None:
        raise ScriptError("Brak kolumny MS_ID_MAGAZYN_TABLE w arkuszu.")

    by_ms_sheet: dict[int, dict[str, Any]] = {}
    by_index_without_ms: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in sheet_rows.values():
        if row["ms_id"]:
            by_ms_sheet[int(row["ms_id"])] = row
        elif row["index"]:
            by_index_without_ms[normalize_key(row["index"])].append(row)

    connection = open_firebird(env)
    try:
        by_ms_fb = firebird_available_devices(connection, warehouse_id=warehouse_id)
    finally:
        connection.close()

    to_fill_ms: list[dict[str, Any]] = []
    to_append: list[dict[str, Any]] = []
    for ms_id, device in by_ms_fb.items():
        if ms_id in by_ms_sheet:
            continue
        index_key = normalize_key(device["index"])
        fill_candidates = [
            row for row in by_index_without_ms.get(index_key, []) if not row["ms_id"]
        ]
        if fill_candidates:
            target = fill_candidates[0]
            to_fill_ms.append(
                {
                    "sheet_row": target["sheet_row"],
                    "index": target["index"],
                    "ms_id_magazyn_table": ms_id,
                }
            )
            fill_candidates.pop(0)
        else:
            to_append.append(device)

    batch_updates: list[dict[str, Any]] = []
    for item in to_fill_ms:
        ms_cell = f"{col_letter(idx.ms_id_magazyn_table + 1)}{item['sheet_row']}"
        batch_updates.append({"range": ms_cell, "values": [[str(item["ms_id_magazyn_table"])]]})

    append_rows_payload: list[list[str]] = []
    for device in to_append:
        row = [""] * len(headers)
        if idx.producer is not None:
            row[idx.producer] = text(device["producer"], limit=50)
        if idx.model is not None:
            row[idx.model] = text(device["model"] or device["name"], limit=250)
        if idx.serial is not None:
            row[idx.serial] = text(device["serial"], limit=100)
        if idx.index is not None:
            row[idx.index] = text(device["index"], limit=100)
        if idx.status is not None:
            row[idx.status] = "01. Przed zerówką"
        if idx.reservation_grenke is not None:
            row[idx.reservation_grenke] = ""
        if idx.proforma_grenke is not None:
            row[idx.proforma_grenke] = ""
        row[idx.ms_id_magazyn_table] = str(device["ms_id_magazyn_table"])
        append_rows_payload.append(row)

    if write:
        if batch_updates:
            worksheet.batch_update(batch_updates, value_input_option="RAW")
        for row in append_rows_payload:
            worksheet.append_row(row, value_input_option="USER_ENTERED")

    report = {
        "generated_at_utc": now_utc().isoformat(),
        "spreadsheet": {
            "id": spreadsheet_id,
            "title": workbook.title,
            "worksheet": worksheet.title,
        },
        "firebird": {
            "warehouse_id": warehouse_id,
            "rows_available": len(by_ms_fb),
        },
        "dry_run": not write,
        "summary": {
            "filled_existing_ms_id_count": len(to_fill_ms),
            "appended_count": len(append_rows_payload),
        },
        "filled_existing_ms_id": to_fill_ms,
        "appended": [
            {
                "ms_id_magazyn_table": int(device["ms_id_magazyn_table"]),
                "index": device["index"],
                "producer": device["producer"],
                "model": device["model"],
            }
            for device in to_append
        ],
    }
    output = save_report(report_dir, "raport_urzadzenia_prod_sync_sheet", report)
    return output


def parse_serial_and_model(model_value: str) -> tuple[str, str]:
    raw = text(model_value)
    if not raw:
        return "", ""
    match = re.search(r"(?i)\bS\s*/?\s*N\s*:\s*([A-Za-z0-9\-_\/]+)", raw)
    if not match:
        return "", raw
    serial = text(match.group(1))
    clean = text(raw[: match.start()])
    clean = re.sub(r"[\s,:;\-]+$", "", clean).strip()
    return serial, clean


def move_serial_from_model(
    env: dict[str, str],
    *,
    spreadsheet_id: str,
    worksheet_title: str,
    report_dir: Path,
    write: bool,
) -> Path:
    workbook, worksheet = open_worksheet(
        env, spreadsheet_id=spreadsheet_id, worksheet_title=worksheet_title
    )
    values = worksheet.get_all_values()
    headers, idx, sheet_rows = parse_sheet_rows(values)
    if idx.model is None or idx.serial is None:
        raise ScriptError("Arkusz nie ma wymaganych kolumn MODEL i SERIAL.")

    updates: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []

    for row in sheet_rows.values():
        serial_before = row["serial"]
        model_before = row["model"]
        serial_from_model, model_after = parse_serial_and_model(model_before)
        if not serial_from_model:
            continue

        should_write_serial = (not serial_before) or serial_before.upper() in {"NIE", "BRAK", "-"}
        row_changed = False
        serial_after = serial_before

        if model_after != model_before:
            cell_model = f"{col_letter(idx.model + 1)}{row['sheet_row']}"
            updates.append({"range": cell_model, "values": [[model_after]]})
            row_changed = True
        if should_write_serial and serial_from_model and serial_from_model != serial_before:
            serial_after = serial_from_model
            cell_serial = f"{col_letter(idx.serial + 1)}{row['sheet_row']}"
            updates.append({"range": cell_serial, "values": [[serial_after]]})
            row_changed = True

        if row_changed:
            changed.append(
                {
                    "sheet_row": row["sheet_row"],
                    "index": row["index"],
                    "serial_before": serial_before,
                    "serial_after": serial_after,
                    "model_before": model_before,
                    "model_after": model_after,
                }
            )

    if write and updates:
        worksheet.batch_update(updates, value_input_option="USER_ENTERED")

    report = {
        "generated_at_utc": now_utc().isoformat(),
        "spreadsheet": {
            "id": spreadsheet_id,
            "title": workbook.title,
            "worksheet": worksheet.title,
        },
        "dry_run": not write,
        "summary": {
            "rows_changed_count": len(changed),
            "updates_count": len(updates),
        },
        "rows_changed": changed,
    }
    output = save_report(report_dir, "raport_urzadzenia_prod_move_serial", report)
    return output


def derive_brand_model(name_value: str) -> tuple[str, str]:
    base = re.split(r"(?i)s\s*/?\s*n\s*:", text(name_value))[0].strip()
    if not base:
        return "", ""
    parts = base.split()
    if len(parts) >= 2 and parts[0].lower() == "konica" and parts[1].lower() == "minolta":
        return "Konica Minolta", " ".join(parts[2:]).strip()
    return parts[0], " ".join(parts[1:]).strip() if len(parts) > 1 else ""


def find_model_id(
    cursor,
    *,
    brand: str,
    model: str,
    name: str,
) -> tuple[int | None, str, str]:
    candidate = text(f"{brand} {model}".strip() or name)
    if not candidate:
        return None, brand, model

    cursor.execute(
        """
        SELECT FIRST 1 ID_MODEL, MARKA, MODEL
        FROM MODEL
        WHERE UPPER(TRIM(MODEL)) = UPPER(TRIM(?))
           OR UPPER(TRIM(MARKA || ' ' || MODEL)) = UPPER(TRIM(?))
        ORDER BY ID_MODEL DESC
        """,
        (candidate, candidate),
    )
    row = cursor.fetchone()
    if row:
        return (
            int(row[0]) if row[0] is not None else None,
            text(row[1]),
            text(row[2]),
        )

    token = text(model or candidate)
    cursor.execute(
        """
        SELECT FIRST 1 ID_MODEL, MARKA, MODEL
        FROM MODEL
        WHERE UPPER(TRIM(MODEL)) CONTAINING UPPER(TRIM(?))
           OR UPPER(TRIM(MARKA || ' ' || MODEL)) CONTAINING UPPER(TRIM(?))
        ORDER BY ID_MODEL DESC
        """,
        (token, token),
    )
    row = cursor.fetchone()
    if row:
        return (
            int(row[0]) if row[0] is not None else None,
            text(row[1]),
            text(row[2]),
        )
    return None, brand, model


def sync_machines(
    env: dict[str, str],
    *,
    report_sheet_path: Path,
    warehouse_client_id: int,
    report_dir: Path,
    write: bool,
) -> Path:
    if not report_sheet_path.exists():
        raise ScriptError(f"Brak raportu wejściowego sync-sheet: {report_sheet_path}")
    payload = json.loads(report_sheet_path.read_text(encoding="utf-8"))

    affected_ids = [
        int(item["ms_id_magazyn_table"]) for item in payload.get("filled_existing_ms_id", [])
    ] + [int(item["ms_id_magazyn_table"]) for item in payload.get("appended", [])]

    connection = open_firebird(env)
    cursor = connection.cursor()
    results: list[dict[str, Any]] = []
    try:
        for ms_id in affected_ids:
            cursor.execute(
                """
                SELECT ID_MAGAZYN_TABLE, INDEKS, NAZWA, MARKA, MODEL, ID_MODEL, SERIAL
                FROM MAGAZYN
                WHERE ID_MAGAZYN_TABLE = ?
                """,
                (ms_id,),
            )
            row = cursor.fetchone()
            if not row:
                results.append(
                    {
                        "ms_id_magazyn_table": ms_id,
                        "action": "skip",
                        "reason": "magazyn_row_not_found",
                    }
                )
                continue

            _, indeks, nazwa, marka, model, id_model, serial = row
            index_value = text(indeks, limit=100)
            name_value = text(nazwa, limit=250)
            serial_value = text(serial, limit=100)
            if serial_value.upper() in {"NIE", "BRAK", "-", "--"}:
                serial_value = ""
            if not serial_value:
                serial_value, _ = parse_serial_and_model(name_value)

            index_token = normalize_firebird_token(index_value)
            cursor.execute(
                """
                SELECT FIRST 1 ID_MASZYNA
                FROM MASZYNA
                WHERE UPPER(REPLACE(REPLACE(REPLACE(COALESCE(EWIDENCJA, ''), '/', ''), '-', ''), ' ', '')) = ?
                ORDER BY ID_MASZYNA DESC
                """,
                (index_token,),
            )
            existing_by_index = cursor.fetchone()
            if existing_by_index:
                results.append(
                    {
                        "ms_id_magazyn_table": ms_id,
                        "index": index_value,
                        "machine_id": int(existing_by_index[0]) if existing_by_index[0] else None,
                        "action": "skip",
                        "reason": "exists_by_ewidencja",
                    }
                )
                continue

            if serial_value:
                serial_token = normalize_firebird_token(serial_value)
                cursor.execute(
                    """
                    SELECT FIRST 1 ID_MASZYNA
                    FROM MASZYNA
                    WHERE UPPER(REPLACE(REPLACE(REPLACE(COALESCE(SERIAL, ''), '/', ''), '-', ''), ' ', '')) = ?
                       OR UPPER(REPLACE(REPLACE(REPLACE(COALESCE(SERIAL2, ''), '/', ''), '-', ''), ' ', '')) = ?
                    ORDER BY ID_MASZYNA DESC
                    """,
                    (serial_token, serial_token),
                )
                existing_by_serial = cursor.fetchone()
                if existing_by_serial:
                    results.append(
                        {
                            "ms_id_magazyn_table": ms_id,
                            "index": index_value,
                            "serial": serial_value,
                            "machine_id": (
                                int(existing_by_serial[0]) if existing_by_serial[0] else None
                            ),
                            "action": "skip",
                            "reason": "exists_by_serial",
                        }
                    )
                    continue

            brand_value = text(marka, limit=100)
            model_value = text(model, limit=100)
            if not brand_value or not model_value:
                parsed_brand, parsed_model = derive_brand_model(name_value)
                brand_value = brand_value or parsed_brand
                model_value = model_value or parsed_model

            model_id_value = int(id_model) if id_model is not None else None
            if model_id_value is None:
                model_id_value, found_brand, found_model = find_model_id(
                    cursor, brand=brand_value, model=model_value, name=name_value
                )
                brand_value = found_brand or brand_value
                model_value = found_model or model_value

            notes_value = text(
                (
                    f"CTIP FLOW sync Urzadzenia_magazyn {now_utc().date().isoformat()} "
                    f"ID_MAGAZYN_TABLE={ms_id}"
                ),
                limit=1000,
            )

            if not write:
                results.append(
                    {
                        "ms_id_magazyn_table": ms_id,
                        "index": index_value,
                        "serial": serial_value,
                        "brand": brand_value,
                        "model": model_value,
                        "id_model": model_id_value,
                        "action": "dry_run_insert",
                        "reason": "would_create",
                    }
                )
                continue

            try:
                cursor.execute(
                    """
                    INSERT INTO MASZYNA (
                        ID_ODDZIAL,
                        ID_FIRMA,
                        ID_KLIENT,
                        ID_MODEL,
                        MARKA,
                        MODEL,
                        SERIAL,
                        EWIDENCJA,
                        AKTYWNA,
                        UWAGI
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    RETURNING ID_MASZYNA
                    """,
                    (
                        1,
                        1,
                        int(warehouse_client_id),
                        model_id_value,
                        text(brand_value, limit=100),
                        text(model_value, limit=100),
                        text(serial_value, limit=100),
                        text(index_value, limit=100),
                        "TAK",
                        notes_value,
                    ),
                )
                inserted = cursor.fetchone()
                connection.commit()
                results.append(
                    {
                        "ms_id_magazyn_table": ms_id,
                        "index": index_value,
                        "serial": serial_value,
                        "brand": brand_value,
                        "model": model_value,
                        "id_model": model_id_value,
                        "machine_id": int(inserted[0]) if inserted and inserted[0] else None,
                        "action": "insert",
                        "reason": "created",
                    }
                )
            except Exception as exc:  # noqa: BLE001
                connection.rollback()
                results.append(
                    {
                        "ms_id_magazyn_table": ms_id,
                        "index": index_value,
                        "serial": serial_value,
                        "brand": brand_value,
                        "model": model_value,
                        "id_model": model_id_value,
                        "action": "error",
                        "reason": str(exc),
                    }
                )
    finally:
        cursor.close()
        connection.close()

    report = {
        "generated_at_utc": now_utc().isoformat(),
        "input_report": str(report_sheet_path),
        "dry_run": not write,
        "warehouse_client_id": warehouse_client_id,
        "summary": {
            "processed": len(results),
            "inserted": sum(1 for item in results if item["action"] == "insert"),
            "exists_by_ewidencja": sum(
                1 for item in results if item["reason"] == "exists_by_ewidencja"
            ),
            "exists_by_serial": sum(1 for item in results if item["reason"] == "exists_by_serial"),
            "dry_run_inserts": sum(1 for item in results if item["action"] == "dry_run_insert"),
            "errors": sum(1 for item in results if item["action"] == "error"),
        },
        "results": results,
    }
    output = save_report(report_dir, "raport_urzadzenia_prod_sync_maszyna", report)
    return output


def append_notes(
    env: dict[str, str],
    *,
    spreadsheet_id: str,
    worksheet_title: str,
    sheet_report: Path | None,
    serial_report: Path | None,
    extra_rows: list[int],
    report_dir: Path,
    write: bool,
) -> Path:
    workbook, worksheet = open_worksheet(
        env, spreadsheet_id=spreadsheet_id, worksheet_title=worksheet_title
    )
    values = worksheet.get_all_values()
    headers, idx, sheet_rows = parse_sheet_rows(values)
    if idx.notes is None:
        raise ScriptError("Arkusz nie zawiera kolumny UWAGI.")
    if idx.codex_notes is None:
        if not write:
            raise ScriptError(
                "Arkusz nie zawiera kolumny UWAGI CODEX (R). "
                "Uruchom ponownie z --apply, aby dodać nagłówek automatycznie."
            )
        headers_updated = list(headers)
        if len(headers_updated) < 18:
            headers_updated.extend([""] * (18 - len(headers_updated)))
        existing_r1 = text(headers_updated[17])
        if existing_r1 and normalize_key(existing_r1) not in {
            normalize_key("UWAGI CODEX"),
            normalize_key("UWAGI KODEX"),
            normalize_key("UWAGI CODEKS"),
        }:
            raise ScriptError(
                f"Kolumna R ma inny nagłówek ({existing_r1!r}). "
                "Ustaw ręcznie 'UWAGI CODEX' i uruchom ponownie."
            )
        headers_updated[17] = "UWAGI CODEX"
        worksheet.update(
            range_name=f"A1:{col_letter(len(headers_updated))}1",
            values=[headers_updated],
            value_input_option="USER_ENTERED",
        )
        values = worksheet.get_all_values()
        headers, idx, sheet_rows = parse_sheet_rows(values)
    if idx.codex_notes is None:
        raise ScriptError("Nie udało się odczytać kolumny UWAGI CODEX po aktualizacji nagłówka.")

    rows_to_update: dict[int, list[str]] = defaultdict(list)

    if sheet_report and sheet_report.exists():
        payload = json.loads(sheet_report.read_text(encoding="utf-8"))
        for item in payload.get("filled_existing_ms_id", []):
            rows_to_update[int(item["sheet_row"])].append("uzupelnienie MS_ID_MAGAZYN_TABLE")

        # Dla dopisanych pozycji wyznaczamy bieżący row po MS_ID (po zapisie arkusza).
        appended_ids = [int(item["ms_id_magazyn_table"]) for item in payload.get("appended", [])]
        ms_to_row = {
            row_data["ms_id"]: row_no
            for row_no, row_data in sheet_rows.items()
            if row_data["ms_id"] is not None
        }
        for ms_id in appended_ids:
            row_no = ms_to_row.get(ms_id)
            if row_no is not None:
                rows_to_update[row_no].append("dopisanie wiersza z Firebird")

    if serial_report and serial_report.exists():
        payload = json.loads(serial_report.read_text(encoding="utf-8"))
        for item in payload.get("rows_changed", []):
            rows_to_update[int(item["sheet_row"])].append("przeniesienie S/N z MODEL do SERIAL")

    for row_no in extra_rows:
        rows_to_update[row_no].append("manualne mapowanie MS_ID")

    stamp = now_utc().strftime("%Y-%m-%d %H:%M UTC")
    updates: list[dict[str, Any]] = []
    changed_rows: list[dict[str, Any]] = []

    for row_no in sorted(rows_to_update):
        row = sheet_rows.get(row_no)
        if row is None:
            continue
        old_notes = row["codex_notes"]
        reasons = ", ".join(sorted(set(rows_to_update[row_no])))
        append_line = f"{NOTES_MARKER} {stamp} - {reasons}."
        if append_line in old_notes:
            continue
        new_notes = old_notes + ("\n" if old_notes else "") + append_line
        cell = f"{col_letter(idx.codex_notes + 1)}{row_no}"
        updates.append({"range": cell, "values": [[new_notes]]})
        changed_rows.append(
            {
                "sheet_row": row_no,
                "index": row["index"],
                "codex_notes_before": old_notes,
                "codex_notes_after": new_notes,
                "reasons": reasons,
            }
        )

    if write and updates:
        worksheet.batch_update(updates, value_input_option="USER_ENTERED")

    report = {
        "generated_at_utc": now_utc().isoformat(),
        "spreadsheet": {
            "id": spreadsheet_id,
            "title": workbook.title,
            "worksheet": worksheet.title,
        },
        "notes_column": "UWAGI CODEX",
        "dry_run": not write,
        "summary": {
            "rows_updated": len(changed_rows),
            "cells_updated": len(updates),
        },
        "rows_updated": changed_rows,
    }
    output = save_report(report_dir, "raport_urzadzenia_prod_append_notes", report)
    return output


def fill_msid_by_index(
    env: dict[str, str],
    *,
    spreadsheet_id: str,
    worksheet_title: str,
    target_row: int,
    report_dir: Path,
    write: bool,
) -> Path:
    workbook, worksheet = open_worksheet(
        env, spreadsheet_id=spreadsheet_id, worksheet_title=worksheet_title
    )
    values = worksheet.get_all_values()
    headers, idx, sheet_rows = parse_sheet_rows(values)
    if idx.index is None or idx.ms_id_magazyn_table is None:
        raise ScriptError("Arkusz nie zawiera kolumn INDEKS/EWIDENCJA lub MS_ID_MAGAZYN_TABLE.")

    row = sheet_rows.get(target_row)
    if row is None:
        raise ScriptError(f"Wiersz {target_row} nie istnieje w arkuszu.")
    if row["ms_id"]:
        raise ScriptError(f"Wiersz {target_row} ma już MS_ID_MAGAZYN_TABLE={row['ms_id']}.")
    index_value = row["index"]
    if not index_value:
        raise ScriptError(f"Wiersz {target_row} nie ma wartości INDEKS/EWIDENCJA.")

    connection = open_firebird(env)
    cursor = connection.cursor()
    try:
        token = normalize_firebird_token(index_value)
        cursor.execute(
            """
            SELECT ID_MAGAZYN_TABLE, INDEKS, NAZWA
            FROM MAGAZYN
            WHERE UPPER(REPLACE(REPLACE(REPLACE(COALESCE(INDEKS, ''), '/', ''), '-', ''), ' ', '')) = ?
            ORDER BY ID_MAGAZYN_TABLE DESC
            """,
            (token,),
        )
        exact = cursor.fetchall()
        if exact:
            candidates = exact
        else:
            base = index_value.split("/")[0]
            cursor.execute(
                """
                SELECT FIRST 20 ID_MAGAZYN_TABLE, INDEKS, NAZWA
                FROM MAGAZYN
                WHERE UPPER(COALESCE(INDEKS, '')) CONTAINING UPPER(?)
                ORDER BY ID_MAGAZYN_TABLE DESC
                """,
                (base,),
            )
            candidates = cursor.fetchall()
    finally:
        cursor.close()
        connection.close()

    if not candidates:
        raise ScriptError(f"Brak dopasowania Firebird dla indeksu: {index_value}")

    chosen = candidates[0]
    chosen_id = int(chosen[0]) if chosen[0] is not None else None
    if not chosen_id:
        raise ScriptError("Nie udało się ustalić ID_MAGAZYN_TABLE z Firebird.")

    cell = f"{col_letter(idx.ms_id_magazyn_table + 1)}{target_row}"
    if write:
        worksheet.update(range_name=cell, values=[[str(chosen_id)]], value_input_option="RAW")

    report = {
        "generated_at_utc": now_utc().isoformat(),
        "spreadsheet": {
            "id": spreadsheet_id,
            "title": workbook.title,
            "worksheet": worksheet.title,
        },
        "dry_run": not write,
        "target": {
            "sheet_row": target_row,
            "index": index_value,
            "ms_id_magazyn_table": chosen_id,
            "cell": cell,
        },
        "candidates": [
            {
                "id_magazyn_table": int(item[0]) if item[0] is not None else None,
                "index": text(item[1]),
                "name": text(item[2]),
            }
            for item in candidates
        ],
    }
    output = save_report(report_dir, "raport_urzadzenia_prod_fill_msid", report)
    return output


def latest_report(report_dir: Path, pattern: str) -> Path:
    matches = sorted(report_dir.glob(pattern))
    if not matches:
        raise ScriptError(f"Brak raportu pasującego do wzorca: {pattern}")
    return matches[-1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audyt i synchronizacja urządzeń FLOW (Firebird <-> arkusz produkcyjny)."
    )
    parser.add_argument("--env-file", default=".env", help="Ścieżka do pliku .env")
    parser.add_argument("--report-dir", default=DEFAULT_REPORT_DIR, help="Katalog raportów JSON")
    parser.add_argument("--spreadsheet-id", default=DEFAULT_SPREADSHEET_ID, help="ID skoroszytu")
    parser.add_argument("--worksheet", default=DEFAULT_WORKSHEET_TITLE, help="Nazwa zakładki")
    parser.add_argument(
        "--warehouse-id",
        type=int,
        default=DEFAULT_WAREHOUSE_ID,
        help="ID magazynu w Firebird (MAGAZYN.ID_MAGAZYN)",
    )
    parser.add_argument(
        "--warehouse-client-id",
        type=int,
        default=DEFAULT_WAREHOUSE_CLIENT_ID,
        help="ID klienta magazynowego dla wpisów MASZYNA",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Wykonaj zapisy (domyślnie tryb dry-run).",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("audit", help="Porównaj Firebird vs arkusz i zapisz raport.")
    subparsers.add_parser("sync-sheet", help="Uzupełnij MS_ID i dopisz brakujące wiersze arkusza.")
    parser_machines = subparsers.add_parser(
        "sync-machines", help="Uzupełnij MASZYNA dla urządzeń z raportu sync-sheet."
    )
    parser_machines.add_argument(
        "--sheet-report",
        default="auto",
        help="Raport sync-sheet (JSON). Wartość 'auto' wybierze najnowszy.",
    )
    subparsers.add_parser(
        "move-serial",
        help="Przenieś numer po S/N: z MODEL do SERIAL i oczyść MODEL.",
    )
    parser_notes = subparsers.add_parser(
        "append-notes",
        help="Dopisz adnotacje [CTIP/CODEX] w kolumnie UWAGI CODEX (R) na podstawie raportów.",
    )
    parser_notes.add_argument(
        "--sheet-report",
        default="auto",
        help="Raport sync-sheet (JSON) albo 'none'.",
    )
    parser_notes.add_argument(
        "--serial-report",
        default="auto",
        help="Raport move-serial (JSON) albo 'none'.",
    )
    parser_notes.add_argument(
        "--extra-row",
        action="append",
        default=[],
        help="Dodatkowy numer wiersza do oznaczenia (można powtórzyć).",
    )
    parser_fill = subparsers.add_parser(
        "fill-msid-by-index",
        help="Uzupełnij MS_ID_MAGAZYN_TABLE w wskazanym wierszu po dopasowaniu indeksu.",
    )
    parser_fill.add_argument(
        "--row", type=int, required=True, help="Numer wiersza arkusza (1-based)."
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    env_file = Path(args.env_file).expanduser()
    report_dir = Path(args.report_dir).expanduser()
    env = load_env_map(env_file)
    write = bool(args.apply)

    command = str(args.command)
    if command == "audit":
        output = report_audit(
            env,
            spreadsheet_id=args.spreadsheet_id,
            worksheet_title=args.worksheet,
            warehouse_id=int(args.warehouse_id),
            report_dir=report_dir,
        )
        print(f"REPORT: {output}")
        return 0

    if command == "sync-sheet":
        output = sync_sheet(
            env,
            spreadsheet_id=args.spreadsheet_id,
            worksheet_title=args.worksheet,
            warehouse_id=int(args.warehouse_id),
            report_dir=report_dir,
            write=write,
        )
        print(f"REPORT: {output}")
        return 0

    if command == "sync-machines":
        sheet_report_arg = str(args.sheet_report)
        sheet_report_path = (
            latest_report(report_dir, "raport_urzadzenia_prod_sync_sheet_*.json")
            if sheet_report_arg == "auto"
            else Path(sheet_report_arg).expanduser()
        )
        output = sync_machines(
            env,
            report_sheet_path=sheet_report_path,
            warehouse_client_id=int(args.warehouse_client_id),
            report_dir=report_dir,
            write=write,
        )
        print(f"REPORT: {output}")
        return 0

    if command == "move-serial":
        output = move_serial_from_model(
            env,
            spreadsheet_id=args.spreadsheet_id,
            worksheet_title=args.worksheet,
            report_dir=report_dir,
            write=write,
        )
        print(f"REPORT: {output}")
        return 0

    if command == "append-notes":
        sheet_arg = str(args.sheet_report)
        serial_arg = str(args.serial_report)
        sheet_report = (
            latest_report(report_dir, "raport_urzadzenia_prod_sync_sheet_*.json")
            if sheet_arg == "auto"
            else None if sheet_arg == "none" else Path(sheet_arg).expanduser()
        )
        serial_report = (
            latest_report(report_dir, "raport_urzadzenia_prod_move_serial_*.json")
            if serial_arg == "auto"
            else None if serial_arg == "none" else Path(serial_arg).expanduser()
        )
        extra_rows = [int(item) for item in args.extra_row]
        output = append_notes(
            env,
            spreadsheet_id=args.spreadsheet_id,
            worksheet_title=args.worksheet,
            sheet_report=sheet_report,
            serial_report=serial_report,
            extra_rows=extra_rows,
            report_dir=report_dir,
            write=write,
        )
        print(f"REPORT: {output}")
        return 0

    if command == "fill-msid-by-index":
        output = fill_msid_by_index(
            env,
            spreadsheet_id=args.spreadsheet_id,
            worksheet_title=args.worksheet,
            target_row=int(args.row),
            report_dir=report_dir,
            write=write,
        )
        print(f"REPORT: {output}")
        return 0

    raise ScriptError(f"Nieznane polecenie: {command}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ScriptError as exc:
        print(f"[ERROR] {exc}")
        raise SystemExit(2) from exc
