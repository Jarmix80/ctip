"""Tylko-odczytowy audyt spójności urządzeń między czterema źródłami."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from app.services.contracts_dashboard import (
    extract_stock_device_identity,
    normalize_device_key,
)
from app.services.firebird_runtime import FirebirdRuntimeConfig
from app.services.workflow_sheet_sync import (
    WorkflowSheetRuntimeConfig,
    workflow_sheet_sync_configured,
)

AUDIT_WORKSHEET_TITLE = "Urzadzenia_magazyn"
AUDIT_WAREHOUSE_ID = 28

_ISSUE_LABELS = {
    "MISSING_SHEET": "brak w aktywnym arkuszu",
    "MISSING_WAREHOUSE": "brak dostępnego stanu w magazynie 28",
    "MISSING_MACHINE": "brak kartoteki urządzenia w Menadżerze Serwisu",
    "MISSING_CTIP": "brak trwałego rejestru CTIP",
    "INVALID_SHEET_MS_ID": "brak lub błędne MS_ID_MAGAZYN_TABLE w arkuszu",
    "SERIAL_MISMATCH": "rozbieżny numer seryjny",
    "EWIDENCJA_MISMATCH": "rozbieżny numer ewidencyjny",
    "MODEL_MISMATCH": "rozbieżny model",
    "DUPLICATE_SHEET": "duplikat w aktywnym arkuszu",
    "DUPLICATE_WAREHOUSE": "duplikat w magazynie",
    "DUPLICATE_MACHINE": "duplikat kartoteki urządzenia",
    "DUPLICATE_CTIP": "duplikat w rejestrze CTIP",
    "MACHINE_INACTIVE": "kartoteka urządzenia jest nieaktywna",
    "CTIP_SHEET_SYNC_FAILED": "ostatnia synchronizacja CTIP z arkuszem zakończyła się błędem",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(str(value or "").strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _normalized_model(value: Any) -> str:
    return normalize_device_key(_text(value))


def _source_record(source: str, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": source,
        "source_row": _positive_int(
            row.get("source_row") or row.get("ms_id_magazyn_table") or row.get("ms_id")
        ),
        "sheet_row": _positive_int(row.get("sheet_row")),
        "machine_id": _positive_int(row.get("machine_id") or row.get("id_maszyna")),
        "ctip_unit_id": _positive_int(row.get("ctip_unit_id") or row.get("id")),
        "producer": _text(row.get("producer") or row.get("marka")),
        "model": _text(row.get("model")),
        "serial": _text(row.get("serial")),
        "ewidencja": _text(row.get("ewidencja") or row.get("index")),
        "active": _text(row.get("active") or row.get("aktywna")),
        "sheet_sync_status": _text(row.get("sheet_sync_status")),
        "raw": row,
    }


def _group_lookup(
    groups: dict[str, list[dict[str, Any]]],
    field: str,
) -> dict[str, set[str]]:
    candidates: dict[str, set[str]] = defaultdict(set)
    for key, records in groups.items():
        for record in records:
            normalized = normalize_device_key(record.get(field))
            if normalized:
                candidates[normalized].add(key)
    return candidates


def _attach_unanchored_records(
    groups: dict[str, list[dict[str, Any]]],
    records: list[dict[str, Any]],
) -> None:
    serial_lookup = _group_lookup(groups, "serial")
    index_lookup = _group_lookup(groups, "ewidencja")
    for ordinal, record in enumerate(records, start=1):
        serial_key = normalize_device_key(record.get("serial"))
        index_key = normalize_device_key(record.get("ewidencja"))
        serial_targets = serial_lookup.get(serial_key, set()) if serial_key else set()
        index_targets = index_lookup.get(index_key, set()) if index_key else set()
        target = next(iter(serial_targets)) if len(serial_targets) == 1 else None
        if target is None and index_key:
            target = next(iter(index_targets)) if len(index_targets) == 1 else None
        if target is None:
            identity = serial_key or index_key or str(ordinal)
            target = f"{record['source']}:{identity}"
            suffix = 1
            while target in groups:
                suffix += 1
                target = f"{record['source']}:{identity}:{suffix}"
            groups[target] = []
        groups[target].append(record)
        if serial_key:
            serial_lookup.setdefault(serial_key, set()).add(target)
        if index_key:
            index_lookup.setdefault(index_key, set()).add(target)


def _different_nonempty(records: list[dict[str, Any]], field: str) -> bool:
    values = {
        (
            _normalized_model(record.get(field))
            if field == "model"
            else normalize_device_key(record.get(field))
        )
        for record in records
        if _text(record.get(field))
    }
    return len(values) > 1


def _first_value(records: list[dict[str, Any]], field: str) -> Any:
    source_order = {"warehouse": 0, "ctip": 1, "sheet": 2, "machine": 3}
    for record in sorted(records, key=lambda item: source_order[item["source"]]):
        value = record.get(field)
        if value not in (None, ""):
            return value
    return None


def _audit_group(key: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_source[record["source"]].append(record)

    issues: list[str] = []
    missing_codes = {
        "sheet": "MISSING_SHEET",
        "warehouse": "MISSING_WAREHOUSE",
        "machine": "MISSING_MACHINE",
        "ctip": "MISSING_CTIP",
    }
    duplicate_codes = {
        "sheet": "DUPLICATE_SHEET",
        "warehouse": "DUPLICATE_WAREHOUSE",
        "machine": "DUPLICATE_MACHINE",
        "ctip": "DUPLICATE_CTIP",
    }
    for source, code in missing_codes.items():
        if not by_source[source]:
            issues.append(code)
    for source, code in duplicate_codes.items():
        if len(by_source[source]) > 1 or any(
            bool(record.get("duplicate_source")) for record in by_source[source]
        ):
            issues.append(code)

    if by_source["sheet"] and any(record["source_row"] is None for record in by_source["sheet"]):
        issues.append("INVALID_SHEET_MS_ID")
    if _different_nonempty(records, "serial"):
        issues.append("SERIAL_MISMATCH")
    if _different_nonempty(records, "ewidencja"):
        issues.append("EWIDENCJA_MISMATCH")
    if _different_nonempty(records, "model"):
        issues.append("MODEL_MISMATCH")
    if any(
        _text(record.get("active")).upper() in {"NIE", "0", "FALSE", "N"}
        for record in by_source["machine"]
    ):
        issues.append("MACHINE_INACTIVE")
    if any(record.get("sheet_sync_status") == "failed" for record in by_source["ctip"]):
        issues.append("CTIP_SHEET_SYNC_FAILED")

    issues = list(dict.fromkeys(issues))
    if any(code.startswith("DUPLICATE_") for code in issues):
        result_status = "duplicate"
    elif any(
        code.endswith("_MISMATCH")
        or code in {"INVALID_SHEET_MS_ID", "MACHINE_INACTIVE", "CTIP_SHEET_SYNC_FAILED"}
        for code in issues
    ):
        result_status = "discrepancy"
    elif issues:
        result_status = "missing"
    else:
        result_status = "ok"

    return {
        "canonical_key": key,
        "producer": _first_value(records, "producer"),
        "model": _first_value(records, "model"),
        "serial": _first_value(records, "serial"),
        "ewidencja": _first_value(records, "ewidencja"),
        "source_row": _first_value(records, "source_row"),
        "sheet_row": _first_value(by_source["sheet"], "sheet_row"),
        "machine_id": _first_value(by_source["machine"], "machine_id"),
        "ctip_unit_id": _first_value(by_source["ctip"], "ctip_unit_id"),
        "sheet_present": bool(by_source["sheet"]),
        "warehouse_present": bool(by_source["warehouse"]),
        "machine_present": len(by_source["machine"]) == 1,
        "ctip_present": len(by_source["ctip"]) == 1,
        "result_status": result_status,
        "issue_codes": issues,
        "issue_summary": "; ".join(_ISSUE_LABELS[code] for code in issues),
        "source_details": {
            source: [record["raw"] for record in by_source[source]]
            for source in ("sheet", "warehouse", "machine", "ctip")
        },
    }


def build_device_audit_items(
    *,
    sheet_rows: list[dict[str, Any]],
    warehouse_rows: list[dict[str, Any]],
    machine_rows: list[dict[str, Any]],
    ctip_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Scala unię źródeł i klasyfikuje rozbieżności bez wykonywania zapisów."""
    records_by_source = {
        "sheet": [_source_record("sheet", row) for row in sheet_rows],
        "warehouse": [_source_record("warehouse", row) for row in warehouse_rows],
        "machine": [_source_record("machine", row) for row in machine_rows],
        "ctip": [_source_record("ctip", row) for row in ctip_rows],
    }
    for source_records in records_by_source.values():
        serial_counts = Counter(
            normalize_device_key(record.get("serial"))
            for record in source_records
            if normalize_device_key(record.get("serial"))
        )
        index_counts = Counter(
            normalize_device_key(record.get("ewidencja"))
            for record in source_records
            if normalize_device_key(record.get("ewidencja"))
        )
        for record in source_records:
            serial_key = normalize_device_key(record.get("serial"))
            index_key = normalize_device_key(record.get("ewidencja"))
            record["duplicate_source"] = bool(serial_key and serial_counts[serial_key] > 1) or bool(
                index_key and index_counts[index_key] > 1
            )
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unanchored: list[dict[str, Any]] = []
    for source in ("warehouse", "ctip", "sheet"):
        for record in records_by_source[source]:
            source_row = record["source_row"]
            if source_row is None:
                unanchored.append(record)
            else:
                groups[f"magazyn:{source_row}"].append(record)
    unanchored.extend(records_by_source["machine"])
    _attach_unanchored_records(groups, unanchored)
    return [
        _audit_group(key, records)
        for key, records in sorted(groups.items(), key=lambda item: item[0])
    ]


def summarize_device_audit(items: list[dict[str, Any]]) -> dict[str, int]:
    """Podsumowuje wynik audytu według końcowej klasyfikacji."""
    statuses = Counter(item["result_status"] for item in items)
    return {
        "total": len(items),
        "ok": statuses["ok"],
        "missing": statuses["missing"],
        "discrepancy": statuses["discrepancy"],
        "duplicate": statuses["duplicate"],
    }


def load_fresh_sheet_rows(config: WorkflowSheetRuntimeConfig) -> tuple[list[dict[str, Any]], dict]:
    """Pobiera świeży odczyt aktywnego arkusza urządzeń w trybie tylko do odczytu."""
    enabled, reason = workflow_sheet_sync_configured(config)
    if not enabled:
        raise RuntimeError(reason or "Konfiguracja Google Sheets jest niepełna.")
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Brak biblioteki Google Sheets: {exc}") from exc
    credentials = Credentials.from_service_account_file(
        config.credentials_path,
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets.readonly",
            "https://www.googleapis.com/auth/drive.readonly",
        ],
    )
    workbook = gspread.authorize(credentials).open_by_key(config.spreadsheet_id)
    worksheet = workbook.worksheet(AUDIT_WORKSHEET_TITLE)
    values = worksheet.get_all_values()
    if not values:
        return [], {
            "spreadsheet_id": workbook.id,
            "worksheet": worksheet.title,
            "rows": 0,
        }

    from app.services.assistant_workflow_devices import parse_sheet_rows

    _, _, parsed = parse_sheet_rows(values)
    rows = list(parsed.values())
    return rows, {
        "spreadsheet_id": workbook.id,
        "worksheet": worksheet.title,
        "rows": len(rows),
    }


def _open_firebird(config: FirebirdRuntimeConfig):
    import firebirdsql  # type: ignore[import-not-found]

    kwargs: dict[str, Any] = {
        "port": config.port,
        "user": config.user,
        "password": config.password,
        "charset": config.charset,
    }
    if config.role:
        kwargs["role"] = config.role
    if config.mode == "network":
        return firebirdsql.connect(host=config.host, database=config.database, **kwargs)
    path = Path(config.local_copy_path)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[2] / path
    return firebirdsql.connect(host="127.0.0.1", database=str(path), **kwargs)


def load_fresh_firebird_rows(
    config: FirebirdRuntimeConfig,
    *,
    warehouse_id: int = AUDIT_WAREHOUSE_ID,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict]:
    """Pobiera dostępny magazyn i wszystkie kartoteki MASZYNA w jednej transakcji RO."""
    connection = _open_firebird(config)
    cursor = connection.cursor()
    try:
        try:
            cursor.execute("SET TRANSACTION READ ONLY")
        except Exception:  # noqa: BLE001
            pass
        cursor.execute(
            """
            SELECT ID_MAGAZYN_TABLE, ID_MODEL, INDEKS, NAZWA, MARKA, MODEL,
                   ILOSC, IL_REZ
            FROM MAGAZYN
            WHERE COALESCE(ID_MAGAZYN, 0) = ?
              AND COALESCE(ILOSC, 0) - COALESCE(IL_REZ, 0) > 0
            ORDER BY ID_MAGAZYN_TABLE
            """,
            (warehouse_id,),
        )
        warehouse_rows = []
        for row in cursor.fetchall():
            identity = extract_stock_device_identity(
                _text(row[3]),
                index_value=_text(row[2]),
                producer=_text(row[4]),
                model=_text(row[5]),
            )
            warehouse_rows.append(
                {
                    "source_row": int(row[0]),
                    "model_id": int(row[1]) if row[1] is not None else None,
                    "ewidencja": identity.get("ewidencja") or _text(row[2]),
                    "serial": identity.get("serial") or "",
                    "producer": _text(row[4]) or _text(identity.get("producer")),
                    "model": _text(row[5]) or _text(identity.get("model")),
                }
            )
        cursor.execute(
            """
            SELECT ID_MASZYNA, ID_MASZYNA_TABLE, ID_MODEL, MARKA, MODEL,
                   SERIAL, EWIDENCJA, AKTYWNA
            FROM MASZYNA
            ORDER BY ID_MASZYNA
            """
        )
        machine_rows = [
            {
                "machine_id": int(row[0]) if row[0] is not None else None,
                "machine_table_id": int(row[1]) if row[1] is not None else None,
                "model_id": int(row[2]) if row[2] is not None else None,
                "producer": _text(row[3]),
                "model": _text(row[4]),
                "serial": _text(row[5]),
                "ewidencja": _text(row[6]),
                "active": _text(row[7]),
            }
            for row in cursor.fetchall()
        ]
        return (
            warehouse_rows,
            machine_rows,
            {
                "warehouse_id": warehouse_id,
                "warehouse_rows": len(warehouse_rows),
                "machine_rows": len(machine_rows),
                "read_only": True,
            },
        )
    finally:
        cursor.close()
        connection.close()


__all__ = [
    "AUDIT_WAREHOUSE_ID",
    "build_device_audit_items",
    "load_fresh_firebird_rows",
    "load_fresh_sheet_rows",
    "summarize_device_audit",
]
