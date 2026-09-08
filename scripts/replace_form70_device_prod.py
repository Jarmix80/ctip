#!/usr/bin/env python3
"""Kontrolowana zamiana urządzenia w produkcyjnym formularzu 70.

Bez parametrów skrypt wykonuje wyłącznie dry-run. Zapis wymaga aktualnego
tokenu stanu, kompletnego backupu oraz dokładnej frazy potwierdzającej.
Operacja zachowuje starą proformę i historię statusu GRENKE.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import socket
import sys
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from app.core.config import SETTINGS_ENV_FILE, settings  # noqa: E402
from app.db.session import AsyncSessionLocal, engine  # noqa: E402
from app.models import (  # noqa: E402
    AdminAuditLog,
    FormRequest,
    FormWorkflowCase,
    FormWorkflowDevice,
)
from app.services.audit import record_audit  # noqa: E402
from app.services.contracts_dashboard import _firebird_connection  # noqa: E402
from app.services.contracts_proforma import (  # noqa: E402
    build_proforma_pdf_storage_path,
    create_proforma_from_workflow,
    delete_proforma_from_firebird,
    ensure_proforma_pdf_file,
)
from app.services.contracts_workflow import (  # noqa: E402
    WORKFLOW_DEVICE_SOURCE_FIREBIRD_WAREHOUSE,
    list_form_workflow_devices,
    replace_form_workflow_devices,
    set_form_workflow_proforma,
)
from app.services.workflow_sheet_status_cache import (  # noqa: E402
    refresh_workflow_sheet_status_cache,
)
from app.services.workflow_sheet_sync import (  # noqa: E402
    WORKFLOW_RESERVATION_STATUS,
    WORKFLOW_SERVICE_BLOCK_STATUS,
    load_workflow_sheet_devices_lookup,
    load_workflow_sheet_runtime_config,
    update_workflow_sheet_fields_for_maintenance,
    use_workflow_sheet_runtime_config,
)

FORM_ID = 70
CASE_ID = 40
TARGET_CLIENT_ID = 1462
WAREHOUSE_CLIENT_ID = 656
BANK_CLIENT_ID = 855

OLD_SOURCE_ROW = 18353
OLD_MACHINE_ID = 7674
OLD_SERIAL = "3930PA00796"
OLD_INDEX = "KP/5116"
OLD_BOUND_EWIDENCJA = "KP/5116/GRENKE"

KEPT_SOURCE_ROW = 18479
KEPT_MACHINE_ID = 7712
KEPT_SERIAL = "3359PA02610"
KEPT_INDEX = "KP/5152"

NEW_SOURCE_ROW = 18839
NEW_MACHINE_ID = 7848
NEW_SERIAL = "3931P651369"
NEW_INDEX = "KP/5278"
NEW_WAREHOUSE_EWIDENCJA = "KP/5278/E"
NEW_BOUND_EWIDENCJA = "KP/5278/GRENKE/E"

OLD_PROFORMA_ID = 64578
OLD_PROFORMA_NUMBER = "52/proforma/2026"
NEW_PRICE_NET = Decimal("7150.0000")
NEW_PRICE_GROSS = Decimal("8794.5000")
KEPT_PRICE_NET = Decimal("6250.0000")
KEPT_PRICE_GROSS = Decimal("7687.5000")
TOTAL_NET = Decimal("13400.0000")
TOTAL_VAT = Decimal("3082.0000")
TOTAL_GROSS = Decimal("16482.0000")

OPERATION_KEY = "form70-device-18353-to-18839-20260908"
APPLY_CONFIRMATION = "ZAMIEN FORMULARZ 70 3930PA00796 NA 3931P651369"
SERVICE_NOTE = "NIESPRAWNE - SERWIS, NIE WYDAWAC (zamiana formularza 70)."
DEFAULT_REPORT_DIR = REPOSITORY_ROOT / "runtime" / "form70_device_replacement"
SHEET_WORKFLOW_FIELDS = (
    "notes",
    "reservation_status",
    "reservation_until",
    "reservation_grenke",
    "form_ctip",
    "proforma_grenke",
    "ctip_form_id",
    "ctip_workflow_case_id",
    "business_status_legacy",
)


class Form70ReplacementError(RuntimeError):
    """Oznacza niezgodny lub niejednoznaczny stan korekty formularza 70."""


def parse_args() -> argparse.Namespace:
    """Parsuje tryb operacji i zabezpieczenia wymagane dla zapisu."""
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--apply", action="store_true", help="Wykonaj zatwierdzony dry-run.")
    modes.add_argument("--rollback", metavar="RUN_ID", help="Wycofaj wskazany przebieg.")
    parser.add_argument("--state-token", default="", help="Token z bezpośredniego dry-run.")
    parser.add_argument("--confirmation", default="", help="Dokładna fraza potwierdzająca.")
    parser.add_argument(
        "--backup-dir",
        type=Path,
        help="Katalog pełnego backupu zawierający pliki PostgreSQL .dump i Firebird .fbk.",
    )
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    return parser.parse_args()


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _state_token(payload: dict[str, Any]) -> str:
    encoded = json.dumps(_json_safe(payload), ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize(value: Any) -> str:
    return " ".join(str(value or "").strip().split()).casefold()


def _money(value: Any) -> Decimal:
    return Decimal(str(value or "0")).quantize(Decimal("0.0001"))


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    return datetime.fromisoformat(text)


def _append_service_note(existing: Any) -> str:
    normalized = str(existing or "").strip()
    if _normalize(SERVICE_NOTE) in _normalize(normalized):
        return normalized
    return f"{normalized}\n{SERVICE_NOTE}".strip()


def _local_ipv4_addresses() -> set[str]:
    """Zwraca adresy IPv4 hosta do ochrony lokalnego DSN produkcji."""
    addresses: set[str] = set()
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addresses.add(str(item[4][0]))
    except OSError:
        pass

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.168.0.8", 9))
        addresses.add(str(probe.getsockname()[0]))
    except OSError:
        pass
    finally:
        probe.close()
    return addresses


def _assert_production_target(*, require_writes: bool = False) -> None:
    if Path(SETTINGS_ENV_FILE).name != ".env":
        raise Form70ReplacementError("Skrypt wymaga jawnego pliku produkcyjnego .env.")
    if settings.ctip_runtime_profile != "production":
        raise Form70ReplacementError("Skrypt wymaga profilu CTIP_RUNTIME_PROFILE=production.")
    pg_host = str(settings.pg_host or "").strip().casefold()
    pg_host_allowed = pg_host == "192.168.0.8" or (
        pg_host in {"127.0.0.1", "localhost"} and "192.168.0.8" in _local_ipv4_addresses()
    )
    if not pg_host_allowed or settings.pg_database != "ctip":
        raise Form70ReplacementError(
            "PostgreSQL musi wskazywać produkcję 192.168.0.8 albo jej lokalny loopback, "
            "baza ctip."
        )
    if settings.fb_host != "192.168.0.8" or settings.fb_port != 3050:
        raise Form70ReplacementError("Firebird musi wskazywać produkcję 192.168.0.8:3050.")
    if require_writes and not settings.fb_allow_writes:
        raise Form70ReplacementError("Zapis Firebird wymaga FB_ALLOW_WRITES=true.")


def _assert_backup(backup_dir: Path | None) -> Path:
    if backup_dir is None:
        raise Form70ReplacementError("Tryb zapisu wymaga parametru --backup-dir.")
    resolved = backup_dir.expanduser().resolve()
    if not resolved.is_dir():
        raise Form70ReplacementError(f"Katalog backupu nie istnieje: {resolved}")
    if not any(resolved.rglob("*.dump")):
        raise Form70ReplacementError("Katalog backupu nie zawiera pliku PostgreSQL .dump.")
    if not any(resolved.rglob("*.fbk")):
        raise Form70ReplacementError("Katalog backupu nie zawiera pliku Firebird .fbk.")
    return resolved


def _device_snapshot(device: FormWorkflowDevice) -> dict[str, Any]:
    return {
        "id": device.id,
        "source_type": device.source_type,
        "source_row": device.source_row,
        "producer": device.producer,
        "model": device.model,
        "serial": device.serial,
        "ewidencja": device.ewidencja,
        "device_status": device.device_status,
        "reservation_status": device.reservation_status,
        "price": device.price,
        "price_net": device.price_net,
        "price_gross": device.price_gross,
        "firebird_machine_id": device.firebird_machine_id,
        "firebird_client_id": device.firebird_client_id,
        "snapshot": deepcopy(device.snapshot or {}),
    }


def _case_snapshot(workflow_case: FormWorkflowCase) -> dict[str, Any]:
    return {
        "id": workflow_case.id,
        "form_request_id": workflow_case.form_request_id,
        "updated_at": workflow_case.updated_at,
        "updated_by": workflow_case.updated_by,
        "stage": workflow_case.stage,
        "business_status": workflow_case.business_status,
        "firebird_client_id": workflow_case.firebird_client_id,
        "proforma_firebird_id": workflow_case.proforma_firebird_id,
        "proforma_number": workflow_case.proforma_number,
        "proforma_pdf_path": workflow_case.proforma_pdf_path,
        "signature_deadline_at": workflow_case.signature_deadline_at,
        "resources_release_due_at": workflow_case.resources_release_due_at,
        "resources_released_at": workflow_case.resources_released_at,
        "status_changed_at": workflow_case.status_changed_at,
        "status_source": workflow_case.status_source,
        "status_history": deepcopy(workflow_case.status_history or []),
    }


async def _load_postgres_state(session: AsyncSession) -> dict[str, Any]:
    form = await session.get(FormRequest, FORM_ID)
    workflow_case = await session.get(FormWorkflowCase, CASE_ID)
    if form is None or workflow_case is None:
        raise Form70ReplacementError("Nie znaleziono formularza 70 albo sprawy workflow 40.")
    devices = await list_form_workflow_devices(session, workflow_case_id=CASE_ID)
    audits = list(
        (
            await session.execute(
                select(AdminAuditLog)
                .where(AdminAuditLog.action == "contracts_flow_proforma_create")
                .order_by(AdminAuditLog.id.desc())
            )
        )
        .scalars()
        .all()
    )
    source_audit = next(
        (
            audit
            for audit in audits
            if int((audit.payload or {}).get("form_request_id") or 0) == FORM_ID
            and int((audit.payload or {}).get("proforma_firebird_id") or 0) == OLD_PROFORMA_ID
        ),
        None,
    )
    actor_id = source_audit.user_id if source_audit is not None else workflow_case.updated_by
    return {
        "form": {
            "id": form.id,
            "status": form.status,
            "archive_bucket": form.archive_bucket,
        },
        "case": _case_snapshot(workflow_case),
        "devices": [_device_snapshot(device) for device in devices],
        "actor_id": actor_id,
        "source_audit_id": source_audit.id if source_audit is not None else None,
        "source_audit_payload": deepcopy(source_audit.payload or {}) if source_audit else {},
    }


def _fetch_rows(cursor, query: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    cursor.execute(query, params)
    columns = [str(item[0]).lower() for item in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _load_proforma(cursor, *, proforma_id: int | None = None, number: str | None = None):
    if proforma_id is not None:
        where = "ID_FAKTURA_TABLE = ?"
        params: tuple[Any, ...] = (proforma_id,)
    elif number:
        where = "NUMER = ? AND RODZAJ_DOK = 'proforma'"
        params = (number,)
    else:
        raise ValueError("Wymagane ID albo numer proformy.")
    headers = _fetch_rows(
        cursor,
        f"""
        SELECT ID_FAKTURA_TABLE, NUMER, DOKUMENT, ID_KLIENT, WYSTAWIL, DATA_WYST,
               SUMA_NETTO, SUMA_VAT, SUMA_BRUTTO, UWAGI, RODZAJ_DOK
        FROM FAKTURA
        WHERE {where}
        """,
        params,
    )
    if not headers:
        return None
    if len(headers) != 1:
        raise Form70ReplacementError(f"Numer proformy {number} nie jest jednoznaczny.")
    header = headers[0]
    header["lines"] = _fetch_rows(
        cursor,
        """
        SELECT ID_FPOZYCJA_TABLE, ID_MAGPOZ, INDEKS, NAZWA, ILOSC,
               CENA_NETTO, CENA_BRUTTO, WARTOSC_NETTO, VAT, WARTOSC_BRUTTO
        FROM FPOZYCJA
        WHERE ID_FAKTURA = ? AND RODZAJ_DOK = 'proforma'
        ORDER BY ID_FPOZYCJA_TABLE
        """,
        (header["id_faktura_table"],),
    )
    return header


def _load_firebird_state(*, planned_proforma_number: str | None = None) -> dict[str, Any]:
    connection = _firebird_connection()
    cursor = connection.cursor()
    try:
        warehouse = _fetch_rows(
            cursor,
            """
            SELECT ID_MAGAZYN_TABLE, ID_MAGAZYN, INDEKS, NAZWA, MARKA, MODEL,
                   ID_MODEL, ILOSC, IL_REZ, VAT_STAWKA
            FROM MAGAZYN
            WHERE ID_MAGAZYN_TABLE IN (?, ?, ?)
            ORDER BY ID_MAGAZYN_TABLE
            """,
            (OLD_SOURCE_ROW, KEPT_SOURCE_ROW, NEW_SOURCE_ROW),
        )
        machines = _fetch_rows(
            cursor,
            """
            SELECT ID_MASZYNA_TABLE, ID_MASZYNA, ID_KLIENT, ID_MODEL, MARKA, MODEL,
                   SERIAL, EWIDENCJA, AKTYWNA, SYNWP
            FROM MASZYNA
            WHERE ID_MASZYNA IN (?, ?, ?)
            ORDER BY ID_MASZYNA
            """,
            (OLD_MACHINE_ID, KEPT_MACHINE_ID, NEW_MACHINE_ID),
        )
        cursor.execute(
            """
            SELECT MAX(DOKUMENT)
            FROM FAKTURA
            WHERE RODZAJ_DOK = 'proforma'
              AND EXTRACT(YEAR FROM COALESCE(DATA_WYST, DATA_SPRZ)) = ?
            """,
            (date.today().year,),
        )
        next_document = int((cursor.fetchone() or [0])[0] or 0) + 1
        planned = planned_proforma_number or f"{next_document}/proforma/{date.today().year}"
        return {
            "warehouse": warehouse,
            "machines": machines,
            "old_proforma": _load_proforma(cursor, proforma_id=OLD_PROFORMA_ID),
            "planned_proforma_number": planned,
            "planned_proforma": _load_proforma(cursor, number=planned),
        }
    finally:
        cursor.close()
        connection.close()


async def _load_sheet_state(session: AsyncSession) -> dict[str, Any]:
    config = await load_workflow_sheet_runtime_config(session)
    with use_workflow_sheet_runtime_config(config):
        lookup = await asyncio.to_thread(load_workflow_sheet_devices_lookup, config)
    if not lookup.get("enabled"):
        raise Form70ReplacementError(str(lookup.get("reason") or "Arkusz FLOW jest wyłączony."))
    by_source = lookup.get("by_source_key") or {}
    rows = {}
    for source_row in (OLD_SOURCE_ROW, KEPT_SOURCE_ROW, NEW_SOURCE_ROW):
        key = f"{WORKFLOW_DEVICE_SOURCE_FIREBIRD_WAREHOUSE}:{source_row}"
        entry = by_source.get(key)
        if not isinstance(entry, dict):
            raise Form70ReplacementError(f"Brak wiersza arkusza dla źródła {source_row}.")
        rows[str(source_row)] = dict(entry)
    return {
        "worksheet_title": lookup.get("worksheet_title"),
        "rows": rows,
    }


async def _load_state(
    session: AsyncSession,
    *,
    planned_proforma_number: str | None = None,
) -> dict[str, Any]:
    postgres = await _load_postgres_state(session)
    firebird, sheet = await asyncio.gather(
        asyncio.to_thread(
            _load_firebird_state,
            planned_proforma_number=planned_proforma_number,
        ),
        _load_sheet_state(session),
    )
    return {"postgres": postgres, "firebird": firebird, "sheet": sheet}


def _by_id(rows: list[dict[str, Any]], key: str) -> dict[int, dict[str, Any]]:
    return {int(row[key]): row for row in rows}


def _assert_device_identity(row: dict[str, Any], *, serial: str, index: str, label: str) -> None:
    actual_serial = _normalize(row.get("serial"))
    serial_in_name = _normalize(serial) in _normalize(row.get("nazwa"))
    if actual_serial != _normalize(serial) and not serial_in_name:
        raise Form70ReplacementError(f"{label}: niezgodny numer seryjny.")
    actual_index = row.get("indeks") if row.get("indeks") is not None else row.get("index")
    if _normalize(actual_index) != _normalize(index):
        raise Form70ReplacementError(f"{label}: niezgodny indeks magazynowy.")


def _validate_old_proforma(proforma: dict[str, Any] | None) -> None:
    if proforma is None:
        raise Form70ReplacementError("Brak starej proformy Firebird ID 64578.")
    if str(proforma.get("numer") or "") != OLD_PROFORMA_NUMBER:
        raise Form70ReplacementError("Stara proforma ma nieoczekiwany numer.")
    if int(proforma.get("id_klient") or 0) != BANK_CLIENT_ID:
        raise Form70ReplacementError("Stara proforma ma nieoczekiwanego odbiorcę.")
    if _money(proforma.get("suma_netto")) != TOTAL_NET:
        raise Form70ReplacementError("Stara proforma ma nieoczekiwaną sumę netto.")
    if _money(proforma.get("suma_vat")) != TOTAL_VAT:
        raise Form70ReplacementError("Stara proforma ma nieoczekiwaną sumę VAT.")
    if _money(proforma.get("suma_brutto")) != TOTAL_GROSS:
        raise Form70ReplacementError("Stara proforma ma nieoczekiwaną sumę brutto.")
    line_rows = {int(line.get("id_magpoz") or 0) for line in proforma.get("lines") or []}
    if line_rows != {OLD_SOURCE_ROW, KEPT_SOURCE_ROW}:
        raise Form70ReplacementError("Stara proforma nie zawiera oczekiwanych dwóch urządzeń.")


def _validate_new_proforma(proforma: dict[str, Any] | None, *, expected_number: str) -> None:
    if proforma is None:
        raise Form70ReplacementError(f"Brak oczekiwanej nowej proformy {expected_number}.")
    if str(proforma.get("numer") or "") != expected_number:
        raise Form70ReplacementError("Nowa proforma ma nieoczekiwany numer.")
    if int(proforma.get("id_klient") or 0) != BANK_CLIENT_ID:
        raise Form70ReplacementError("Nowa proforma ma nieoczekiwanego odbiorcę.")
    if _money(proforma.get("suma_netto")) != TOTAL_NET:
        raise Form70ReplacementError("Nowa proforma ma nieoczekiwaną sumę netto.")
    if _money(proforma.get("suma_vat")) != TOTAL_VAT:
        raise Form70ReplacementError("Nowa proforma ma nieoczekiwaną sumę VAT.")
    if _money(proforma.get("suma_brutto")) != TOTAL_GROSS:
        raise Form70ReplacementError("Nowa proforma ma nieoczekiwaną sumę brutto.")
    lines = list(proforma.get("lines") or [])
    if [int(line.get("id_magpoz") or 0) for line in lines] != [NEW_SOURCE_ROW, KEPT_SOURCE_ROW]:
        raise Form70ReplacementError("Nowa proforma ma nieoczekiwaną kolejność urządzeń.")
    prices = {
        int(line.get("id_magpoz") or 0): (
            _money(line.get("cena_netto")),
            _money(line.get("cena_brutto")),
        )
        for line in lines
    }
    if prices.get(NEW_SOURCE_ROW) != (NEW_PRICE_NET, NEW_PRICE_GROSS):
        raise Form70ReplacementError("Nowe urządzenie ma nieoczekiwaną cenę na proformie.")
    if prices.get(KEPT_SOURCE_ROW) != (KEPT_PRICE_NET, KEPT_PRICE_GROSS):
        raise Form70ReplacementError("Pozostawione urządzenie ma nieoczekiwaną cenę.")


def validate_initial_state(state: dict[str, Any]) -> None:
    """Waliduje dokładny stan wejściowy uzgodnionej korekty produkcyjnej."""
    postgres = state["postgres"]
    workflow_case = postgres["case"]
    if postgres["form"] != {"id": FORM_ID, "status": "SUBMITTED", "archive_bucket": None}:
        raise Form70ReplacementError("Formularz 70 nie jest aktywnym formularzem SUBMITTED.")
    if int(workflow_case.get("form_request_id") or 0) != FORM_ID:
        raise Form70ReplacementError("Sprawa 40 nie należy do formularza 70.")
    if workflow_case.get("stage") != "PROFORMA_CREATED":
        raise Form70ReplacementError("Sprawa 40 nie jest na etapie PROFORMA_CREATED.")
    if workflow_case.get("business_status") != "APPROVED_ORDER":
        raise Form70ReplacementError("Sprawa 40 nie ma statusu APPROVED_ORDER.")
    if workflow_case.get("status_source") != "mailbox":
        raise Form70ReplacementError("Status sprawy 40 nie pochodzi z mailboxa GRENKE.")
    if int(workflow_case.get("firebird_client_id") or 0) != TARGET_CLIENT_ID:
        raise Form70ReplacementError("Sprawa 40 ma nieoczekiwanego klienta MS.")
    if int(workflow_case.get("proforma_firebird_id") or 0) != OLD_PROFORMA_ID:
        raise Form70ReplacementError("Sprawa 40 nie wskazuje starej proformy 64578.")
    if workflow_case.get("proforma_number") != OLD_PROFORMA_NUMBER:
        raise Form70ReplacementError("Sprawa 40 ma nieoczekiwany numer starej proformy.")

    devices = _by_id(postgres["devices"], "source_row")
    if set(devices) != {OLD_SOURCE_ROW, KEPT_SOURCE_ROW}:
        raise Form70ReplacementError("Formularz 70 nie zawiera oczekiwanych dwóch urządzeń.")
    _assert_device_identity(
        {"serial": devices[OLD_SOURCE_ROW]["serial"], "indeks": OLD_INDEX},
        serial=OLD_SERIAL,
        index=OLD_INDEX,
        label="Stare urządzenie CTIP",
    )
    _assert_device_identity(
        {"serial": devices[KEPT_SOURCE_ROW]["serial"], "indeks": KEPT_INDEX},
        serial=KEPT_SERIAL,
        index=KEPT_INDEX,
        label="Pozostawione urządzenie CTIP",
    )
    if _money(devices[OLD_SOURCE_ROW]["price_net"]) != NEW_PRICE_NET:
        raise Form70ReplacementError("Stare urządzenie CTIP ma nieoczekiwaną cenę netto.")
    if _money(devices[OLD_SOURCE_ROW]["price_gross"]) != NEW_PRICE_GROSS:
        raise Form70ReplacementError("Stare urządzenie CTIP ma nieoczekiwaną cenę brutto.")
    if _money(devices[KEPT_SOURCE_ROW]["price_net"]) != KEPT_PRICE_NET:
        raise Form70ReplacementError("Pozostawione urządzenie CTIP ma nieoczekiwaną cenę netto.")
    if _money(devices[KEPT_SOURCE_ROW]["price_gross"]) != KEPT_PRICE_GROSS:
        raise Form70ReplacementError("Pozostawione urządzenie CTIP ma nieoczekiwaną cenę brutto.")

    firebird = state["firebird"]
    warehouse = _by_id(firebird["warehouse"], "id_magazyn_table")
    if set(warehouse) != {OLD_SOURCE_ROW, KEPT_SOURCE_ROW, NEW_SOURCE_ROW}:
        raise Form70ReplacementError("Firebird nie zwrócił trzech oczekiwanych pozycji MAGAZYN.")
    _assert_device_identity(
        warehouse[OLD_SOURCE_ROW], serial=OLD_SERIAL, index=OLD_INDEX, label="MAGAZYN stare"
    )
    _assert_device_identity(
        warehouse[KEPT_SOURCE_ROW],
        serial=KEPT_SERIAL,
        index=KEPT_INDEX,
        label="MAGAZYN pozostawione",
    )
    _assert_device_identity(
        warehouse[NEW_SOURCE_ROW], serial=NEW_SERIAL, index=NEW_INDEX, label="MAGAZYN nowe"
    )

    machines = _by_id(firebird["machines"], "id_maszyna")
    if set(machines) != {OLD_MACHINE_ID, KEPT_MACHINE_ID, NEW_MACHINE_ID}:
        raise Form70ReplacementError("Firebird nie zwrócił trzech oczekiwanych kartotek MASZYNA.")
    if int(machines[OLD_MACHINE_ID].get("id_klient") or 0) != TARGET_CLIENT_ID:
        raise Form70ReplacementError("Stara maszyna nie jest przypisana do klienta 1462.")
    if _normalize(machines[OLD_MACHINE_ID].get("ewidencja")) != _normalize(OLD_BOUND_EWIDENCJA):
        raise Form70ReplacementError("Stara maszyna ma nieoczekiwaną ewidencję GRENKE.")
    if int(machines[NEW_MACHINE_ID].get("id_klient") or 0) != WAREHOUSE_CLIENT_ID:
        raise Form70ReplacementError("Nowa maszyna nie jest przypisana do magazynu 656.")
    if _normalize(machines[NEW_MACHINE_ID].get("ewidencja")) != _normalize(NEW_WAREHOUSE_EWIDENCJA):
        raise Form70ReplacementError("Nowa maszyna ma nieoczekiwaną ewidencję magazynową.")
    if int(machines[KEPT_MACHINE_ID].get("id_klient") or 0) != TARGET_CLIENT_ID:
        raise Form70ReplacementError("Pozostawiona maszyna nie jest przypisana do klienta 1462.")
    _validate_old_proforma(firebird["old_proforma"])
    if firebird.get("planned_proforma") is not None:
        raise Form70ReplacementError(
            f"Planowany numer {firebird['planned_proforma_number']} jest już zajęty."
        )

    sheet_rows = state["sheet"]["rows"]
    for source_row, serial, index in (
        (OLD_SOURCE_ROW, OLD_SERIAL, OLD_INDEX),
        (KEPT_SOURCE_ROW, KEPT_SERIAL, KEPT_INDEX),
        (NEW_SOURCE_ROW, NEW_SERIAL, NEW_INDEX),
    ):
        row = sheet_rows[str(source_row)]
        _assert_device_identity(row, serial=serial, index=index, label=f"Arkusz {source_row}")
    old_sheet = sheet_rows[str(OLD_SOURCE_ROW)]
    kept_sheet = sheet_rows[str(KEPT_SOURCE_ROW)]
    new_sheet = sheet_rows[str(NEW_SOURCE_ROW)]
    for label, row in (("stare", old_sheet), ("pozostawione", kept_sheet)):
        if row.get("reservation_status") != WORKFLOW_RESERVATION_STATUS:
            raise Form70ReplacementError(f"Arkusz {label}: brak rezerwacji GRENKE.")
        if str(row.get("ctip_form_id") or "") != str(FORM_ID):
            raise Form70ReplacementError(f"Arkusz {label}: nieoczekiwany CTIP_FORM_ID.")
        if str(row.get("ctip_workflow_case_id") or "") != str(CASE_ID):
            raise Form70ReplacementError(f"Arkusz {label}: nieoczekiwany CTIP_WORKFLOW_CASE_ID.")
        if row.get("proforma_grenke") != OLD_PROFORMA_NUMBER:
            raise Form70ReplacementError(f"Arkusz {label}: nieoczekiwana stara proforma.")
    if _normalize(new_sheet.get("reservation_status")) != _normalize("brak rezerwacji"):
        raise Form70ReplacementError("Nowe urządzenie ma aktywną rezerwację w arkuszu.")
    if any(
        str(new_sheet.get(key) or "").strip()
        for key in ("form_ctip", "ctip_form_id", "ctip_workflow_case_id")
    ):
        raise Form70ReplacementError("Nowe urządzenie ma nieoczekiwane powiązanie FLOW w arkuszu.")
    new_sheet_proforma = str(new_sheet.get("proforma_grenke") or "").strip()
    if new_sheet_proforma not in {"", OLD_PROFORMA_NUMBER}:
        raise Form70ReplacementError("Nowe urządzenie ma nieoczekiwaną proformę FLOW w arkuszu.")


def _public_summary(state: dict[str, Any]) -> dict[str, Any]:
    workflow_case = state["postgres"]["case"]
    devices = state["postgres"]["devices"]
    machines = _by_id(state["firebird"]["machines"], "id_maszyna")
    sheet_rows = state["sheet"]["rows"]
    return {
        "form_id": FORM_ID,
        "workflow_case_id": CASE_ID,
        "business_status": workflow_case.get("business_status"),
        "status_source": workflow_case.get("status_source"),
        "current_proforma": workflow_case.get("proforma_number"),
        "planned_proforma": state["firebird"].get("planned_proforma_number"),
        "selected_devices": [
            {
                "source_row": item.get("source_row"),
                "serial": item.get("serial"),
                "price_net": item.get("price_net"),
                "price_gross": item.get("price_gross"),
            }
            for item in devices
        ],
        "machine_owners": {
            str(machine_id): machines[machine_id].get("id_klient")
            for machine_id in (OLD_MACHINE_ID, KEPT_MACHINE_ID, NEW_MACHINE_ID)
        },
        "sheet": {
            key: {
                "sheet_row": value.get("sheet_row"),
                "serial": value.get("serial"),
                "reservation_status": value.get("reservation_status"),
                "form_ctip": value.get("form_ctip"),
                "proforma_grenke": value.get("proforma_grenke"),
            }
            for key, value in sheet_rows.items()
        },
    }


def _journal_path(report_dir: Path) -> Path:
    return report_dir / f"{OPERATION_KEY}.json"


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(_json_safe(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_report(report_dir: Path, payload: dict[str, Any]) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = report_dir / f"form70_device_replacement_{payload['mode']}_{stamp}.json"
    _write_json_atomic(path, payload)
    return path


def _load_journal(report_dir: Path, *, run_id: str | None = None) -> dict[str, Any]:
    path = _journal_path(report_dir)
    if not path.is_file():
        raise Form70ReplacementError(f"Brak dziennika operacji: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("operation_key") != OPERATION_KEY:
        raise Form70ReplacementError("Dziennik ma niezgodny identyfikator operacji.")
    if run_id is not None and payload.get("run_id") != str(UUID(run_id)):
        raise Form70ReplacementError("Dziennik nie należy do wskazanego przebiegu.")
    return payload


def _save_journal(report_dir: Path, journal: dict[str, Any], *, phase: str) -> None:
    journal["phase"] = phase
    journal["updated_at"] = datetime.now(UTC)
    _write_json_atomic(_journal_path(report_dir), journal)


def _copy_old_pdf(report_dir: Path) -> dict[str, Any]:
    source = build_proforma_pdf_storage_path(OLD_PROFORMA_ID)
    if not source.is_file():
        ensure_proforma_pdf_file(OLD_PROFORMA_ID)
    if not source.is_file():
        raise Form70ReplacementError(f"Nie udało się odczytać starego PDF: {source}")
    target_dir = report_dir / "protected_old_proforma"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / source.name
    shutil.copy2(source, target)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    return {"source": str(source), "copy": str(target), "sha256": digest}


def _proforma_devices(state: dict[str, Any]) -> list[dict[str, Any]]:
    warehouse = _by_id(state["firebird"]["warehouse"], "id_magazyn_table")
    return [
        {
            "row": NEW_SOURCE_ROW,
            "source_type": WORKFLOW_DEVICE_SOURCE_FIREBIRD_WAREHOUSE,
            "producer": warehouse[NEW_SOURCE_ROW].get("marka") or "Ricoh",
            "model": warehouse[NEW_SOURCE_ROW].get("model") or "IM C300",
            "serial": NEW_SERIAL,
            "ewidencja": NEW_INDEX,
            "index": NEW_INDEX,
            "price_net": format(NEW_PRICE_NET, "f"),
            "price_gross": format(NEW_PRICE_GROSS, "f"),
            "vat_rate": "23",
        },
        {
            "row": KEPT_SOURCE_ROW,
            "source_type": WORKFLOW_DEVICE_SOURCE_FIREBIRD_WAREHOUSE,
            "producer": warehouse[KEPT_SOURCE_ROW].get("marka") or "Ricoh",
            "model": warehouse[KEPT_SOURCE_ROW].get("model") or "IM 430",
            "serial": KEPT_SERIAL,
            "ewidencja": KEPT_INDEX,
            "index": KEPT_INDEX,
            "price_net": format(KEPT_PRICE_NET, "f"),
            "price_gross": format(KEPT_PRICE_GROSS, "f"),
            "vat_rate": "23",
        },
    ]


def _ensure_new_proforma(journal: dict[str, Any]) -> dict[str, Any]:
    planned_number = str(journal["planned_proforma_number"])
    current = _load_firebird_state(planned_proforma_number=planned_number)
    existing = current.get("planned_proforma")
    if existing is not None:
        _validate_new_proforma(existing, expected_number=planned_number)
        proforma_id = int(existing["id_faktura_table"])
        pdf_path = ensure_proforma_pdf_file(proforma_id)
        return {
            "id": proforma_id,
            "number": planned_number,
            "pdf_path": pdf_path.as_posix(),
            "created": False,
        }

    old_proforma = current.get("old_proforma")
    _validate_old_proforma(old_proforma)
    issuer = str(old_proforma.get("wystawil") or "").strip()
    if not issuer:
        raise Form70ReplacementError("Stara proforma nie ma wystawcy do skopiowania.")
    result = create_proforma_from_workflow(
        form_request_id=FORM_ID,
        firebird_client_id=BANK_CLIENT_ID,
        selected_devices=_proforma_devices(journal["before"]),
        issuer_name=issuer,
    )
    if result.document_number != planned_number:
        raise Form70ReplacementError(
            f"Utworzono numer {result.document_number}, oczekiwano {planned_number}."
        )
    verified = _load_firebird_state(planned_proforma_number=planned_number)["planned_proforma"]
    _validate_new_proforma(verified, expected_number=planned_number)
    return {
        "id": result.id_faktura_table,
        "number": result.document_number,
        "pdf_path": result.pdf_path or result.preview_url,
        "created": True,
    }


def _apply_machine_replacement() -> dict[str, Any]:
    connection = _firebird_connection()
    cursor = connection.cursor()
    try:
        machines = _by_id(
            _fetch_rows(
                cursor,
                """
                SELECT ID_MASZYNA, ID_KLIENT, SERIAL, EWIDENCJA
                FROM MASZYNA
                WHERE ID_MASZYNA IN (?, ?, ?)
                ORDER BY ID_MASZYNA
                """,
                (OLD_MACHINE_ID, KEPT_MACHINE_ID, NEW_MACHINE_ID),
            ),
            "id_maszyna",
        )
        if set(machines) != {OLD_MACHINE_ID, KEPT_MACHINE_ID, NEW_MACHINE_ID}:
            raise Form70ReplacementError("Brak oczekiwanych kartotek MASZYNA przed zapisem.")

        transitions = (
            (
                OLD_MACHINE_ID,
                TARGET_CLIENT_ID,
                OLD_BOUND_EWIDENCJA,
                WAREHOUSE_CLIENT_ID,
                OLD_INDEX,
            ),
            (
                NEW_MACHINE_ID,
                WAREHOUSE_CLIENT_ID,
                NEW_WAREHOUSE_EWIDENCJA,
                TARGET_CLIENT_ID,
                NEW_BOUND_EWIDENCJA,
            ),
        )
        changed = []
        for machine_id, source_client, source_index, target_client, target_index in transitions:
            row = machines[machine_id]
            current = (int(row.get("id_klient") or 0), _normalize(row.get("ewidencja")))
            source = (source_client, _normalize(source_index))
            target = (target_client, _normalize(target_index))
            if current == target:
                continue
            if current != source:
                raise Form70ReplacementError(
                    f"MASZYNA {machine_id} ma nieoczekiwanego właściciela lub ewidencję."
                )
            cursor.execute(
                """
                UPDATE MASZYNA
                SET ID_KLIENT = ?, EWIDENCJA = ?
                WHERE ID_MASZYNA = ? AND ID_KLIENT = ? AND EWIDENCJA = ?
                """,
                (target_client, target_index, machine_id, source_client, source_index),
            )
            if int(getattr(cursor, "rowcount", 0) or 0) != 1:
                raise Form70ReplacementError(f"Nie udało się zaktualizować MASZYNA {machine_id}.")
            changed.append(machine_id)

        kept = machines[KEPT_MACHINE_ID]
        if int(kept.get("id_klient") or 0) != TARGET_CLIENT_ID:
            raise Form70ReplacementError("Pozostawiona maszyna zmieniła właściciela.")
        connection.commit()
        return {"changed_machine_ids": changed}
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


def _device_restore_payload(device: dict[str, Any]) -> dict[str, Any]:
    snapshot = dict(device.get("snapshot") or {})
    return {
        **snapshot,
        "row": device.get("source_row"),
        "source_type": device.get("source_type"),
        "producer": device.get("producer"),
        "model": device.get("model"),
        "serial": device.get("serial"),
        "ewidencja": device.get("ewidencja"),
        "status": device.get("device_status"),
        "reservation_status": device.get("reservation_status"),
        "price": device.get("price"),
        "price_net": device.get("price_net"),
        "price_gross": device.get("price_gross"),
        "ms_id_maszyna": device.get("firebird_machine_id"),
        "ms_id_klient": device.get("firebird_client_id"),
    }


async def _apply_postgres_replacement(
    session: AsyncSession,
    *,
    journal: dict[str, Any],
) -> dict[str, Any]:
    state = await _load_postgres_state(session)
    workflow_case = await session.get(FormWorkflowCase, CASE_ID)
    if workflow_case is None:
        raise Form70ReplacementError("Brak sprawy 40 podczas zapisu PostgreSQL.")
    devices = await list_form_workflow_devices(session, workflow_case_id=CASE_ID)
    by_source = {int(item.source_row or 0): item for item in devices}
    target_proforma_id = int(journal["new_proforma"]["id"])
    target_proforma_number = str(journal["new_proforma"]["number"])
    if (
        set(by_source) == {KEPT_SOURCE_ROW, NEW_SOURCE_ROW}
        and workflow_case.proforma_firebird_id == target_proforma_id
        and workflow_case.proforma_number == target_proforma_number
    ):
        return {"changed": False, "device_ids": [item.id for item in devices]}
    if set(by_source) != {OLD_SOURCE_ROW, KEPT_SOURCE_ROW}:
        raise Form70ReplacementError("PostgreSQL ma nieoczekiwany zestaw urządzeń przed zamianą.")
    if workflow_case.proforma_firebird_id != OLD_PROFORMA_ID:
        raise Form70ReplacementError("PostgreSQL nie wskazuje starej proformy przed zamianą.")

    status_guard = {
        "business_status": workflow_case.business_status,
        "status_source": workflow_case.status_source,
        "status_changed_at": workflow_case.status_changed_at,
        "status_history": deepcopy(workflow_case.status_history or []),
        "signature_deadline_at": workflow_case.signature_deadline_at,
        "resources_release_due_at": workflow_case.resources_release_due_at,
        "resources_released_at": workflow_case.resources_released_at,
    }
    kept_payload = _device_restore_payload(_device_snapshot(by_source[KEPT_SOURCE_ROW]))
    kept_payload["sheet_proforma_number"] = target_proforma_number
    new_sheet = journal["before"]["sheet"]["rows"][str(NEW_SOURCE_ROW)]
    now = datetime.now(UTC).isoformat()
    new_payload = {
        "row": NEW_SOURCE_ROW,
        "source_type": WORKFLOW_DEVICE_SOURCE_FIREBIRD_WAREHOUSE,
        "producer": "Ricoh",
        "model": "IM C300",
        "serial": NEW_SERIAL,
        "ewidencja": NEW_BOUND_EWIDENCJA,
        "index": NEW_BOUND_EWIDENCJA,
        "status": new_sheet.get("status") or "02. Po zerówce",
        "reservation_status": WORKFLOW_RESERVATION_STATUS,
        "price": format(NEW_PRICE_GROSS, "f"),
        "price_net": format(NEW_PRICE_NET, "f"),
        "price_gross": format(NEW_PRICE_GROSS, "f"),
        "vat_rate": "23",
        "ms_id_magazyn_table": NEW_SOURCE_ROW,
        "ms_id_maszyna": NEW_MACHINE_ID,
        "ms_id_klient": TARGET_CLIENT_ID,
        "sheet_row": int(new_sheet.get("sheet_row") or 0),
        "sheet_sync_status": "synced",
        "sheet_sync_updated_at": now,
        "sheet_assignee": journal["sheet_reservation_label"].split("\n", 1)[0],
        "sheet_assignee_id": state.get("source_audit_payload", {}).get("sheet_assignee_id"),
        "sheet_proforma_number": target_proforma_number,
        "ctip_form_id": FORM_ID,
        "ctip_workflow_case_id": CASE_ID,
        "ms_binding_status": "ok",
        "ms_binding_message": f"Powiązano istniejący rekord MASZYNA ID {NEW_MACHINE_ID}.",
        "ms_binding_updated_at": now,
        "ms_binding_error": None,
    }
    updated_devices = await replace_form_workflow_devices(
        session,
        workflow_case=workflow_case,
        selected_devices=[new_payload, kept_payload],
        updated_by=journal.get("actor_id"),
    )
    for device in updated_devices:
        snapshot = dict(device.snapshot or {})
        snapshot["sheet_proforma_number"] = target_proforma_number
        snapshot["sheet_sync_status"] = "synced"
        snapshot["sheet_sync_updated_at"] = now
        if device.source_row == NEW_SOURCE_ROW:
            snapshot["ms_binding_status"] = "ok"
            snapshot["ms_binding_message"] = (
                f"Powiązano istniejący rekord MASZYNA ID {NEW_MACHINE_ID}."
            )
            snapshot["ms_binding_updated_at"] = now
            snapshot["ms_binding_error"] = None
        device.snapshot = snapshot

    await set_form_workflow_proforma(
        session,
        workflow_case=workflow_case,
        proforma_firebird_id=target_proforma_id,
        proforma_number=target_proforma_number,
        proforma_pdf_path=str(journal["new_proforma"]["pdf_path"]),
        updated_by=journal.get("actor_id"),
    )
    for key, expected in status_guard.items():
        if getattr(workflow_case, key) != expected:
            raise Form70ReplacementError(f"Korekta próbowała zmienić chronione pole {key}.")
    await record_audit(
        session,
        user_id=journal.get("actor_id"),
        action="contracts_flow_device_replacement",
        client_ip=None,
        payload={
            "operation_key": OPERATION_KEY,
            "run_id": journal["run_id"],
            "form_request_id": FORM_ID,
            "workflow_case_id": CASE_ID,
            "old_device": {"source_row": OLD_SOURCE_ROW, "serial": OLD_SERIAL},
            "new_device": {"source_row": NEW_SOURCE_ROW, "serial": NEW_SERIAL},
            "old_proforma_firebird_id": OLD_PROFORMA_ID,
            "old_proforma_number": OLD_PROFORMA_NUMBER,
            "new_proforma_firebird_id": target_proforma_id,
            "new_proforma_number": target_proforma_number,
            "business_status_preserved": workflow_case.business_status,
            "status_source_preserved": workflow_case.status_source,
        },
    )
    await session.commit()
    return {"changed": True, "device_ids": [item.id for item in updated_devices]}


def _sheet_target_changes(journal: dict[str, Any]) -> list[dict[str, Any]]:
    before_rows = journal["before"]["sheet"]["rows"]
    new_number = str(journal["new_proforma"]["number"])
    reservation_label = str(journal["sheet_reservation_label"])
    business_label = str(journal["sheet_business_status_label"])
    changes = []
    for source_row, serial, index in (
        (OLD_SOURCE_ROW, OLD_SERIAL, OLD_INDEX),
        (KEPT_SOURCE_ROW, KEPT_SERIAL, KEPT_INDEX),
        (NEW_SOURCE_ROW, NEW_SERIAL, NEW_INDEX),
    ):
        before = before_rows[str(source_row)]
        expected = {key: before.get(key) or "" for key in SHEET_WORKFLOW_FIELDS}
        if source_row == OLD_SOURCE_ROW:
            target = {
                "notes": _append_service_note(before.get("notes")),
                "reservation_status": WORKFLOW_SERVICE_BLOCK_STATUS,
                "reservation_until": "",
                "reservation_grenke": "",
                "form_ctip": "",
                "proforma_grenke": "",
                "ctip_form_id": "",
                "ctip_workflow_case_id": "",
                "business_status_legacy": "",
            }
        else:
            target = {
                "notes": before.get("notes") or "",
                "reservation_status": WORKFLOW_RESERVATION_STATUS,
                "reservation_until": "",
                "reservation_grenke": reservation_label,
                "form_ctip": str(FORM_ID),
                "proforma_grenke": new_number,
                "ctip_form_id": str(FORM_ID),
                "ctip_workflow_case_id": str(CASE_ID),
                "business_status_legacy": business_label,
            }
        changes.append(
            {
                "device": {
                    "source_row": source_row,
                    "sheet_row": before.get("sheet_row"),
                    "serial": serial,
                    "index": index,
                },
                "expected_fields": expected,
                "target_fields": target,
                "background": "reserved",
            }
        )
    return changes


async def _apply_sheet_replacement(
    session: AsyncSession,
    *,
    journal: dict[str, Any],
) -> dict[str, Any]:
    config = await load_workflow_sheet_runtime_config(session)
    with use_workflow_sheet_runtime_config(config):
        result = await asyncio.to_thread(
            update_workflow_sheet_fields_for_maintenance,
            changes=_sheet_target_changes(journal),
        )
    if not result.get("enabled") or int(result.get("updated_count") or 0) != 3:
        raise Form70ReplacementError(
            str(result.get("reason") or "Nie zaktualizowano trzech wierszy arkusza.")
        )
    cache_result = await refresh_workflow_sheet_status_cache(
        session,
        user_id=journal.get("actor_id"),
    )
    if not cache_result.get("success"):
        raise Form70ReplacementError(
            str(cache_result.get("message") or "Nie odświeżono cache arkusza.")
        )
    return {"sheet": result, "cache": cache_result}


def _validate_completed_state(state: dict[str, Any], journal: dict[str, Any]) -> None:
    new_proforma = journal["new_proforma"]
    postgres = state["postgres"]
    workflow_case = postgres["case"]
    devices = _by_id(postgres["devices"], "source_row")
    if set(devices) != {KEPT_SOURCE_ROW, NEW_SOURCE_ROW}:
        raise Form70ReplacementError("Po korekcie PostgreSQL ma nieoczekiwane urządzenia.")
    if int(workflow_case.get("proforma_firebird_id") or 0) != int(new_proforma["id"]):
        raise Form70ReplacementError("Po korekcie sprawa nie wskazuje nowej proformy.")
    if workflow_case.get("proforma_number") != new_proforma["number"]:
        raise Form70ReplacementError("Po korekcie sprawa ma nieoczekiwany numer proformy.")
    before_case = journal["before"]["postgres"]["case"]
    for key in (
        "business_status",
        "status_source",
        "status_changed_at",
        "status_history",
        "signature_deadline_at",
        "resources_release_due_at",
        "resources_released_at",
    ):
        if _json_safe(workflow_case.get(key)) != _json_safe(before_case.get(key)):
            raise Form70ReplacementError(f"Chronione pole sprawy {key} uległo zmianie.")

    firebird = state["firebird"]
    _validate_old_proforma(firebird["old_proforma"])
    _validate_new_proforma(
        firebird["planned_proforma"],
        expected_number=str(new_proforma["number"]),
    )
    machines = _by_id(firebird["machines"], "id_maszyna")
    expected_machines = {
        OLD_MACHINE_ID: (WAREHOUSE_CLIENT_ID, OLD_INDEX),
        KEPT_MACHINE_ID: (TARGET_CLIENT_ID, machines[KEPT_MACHINE_ID].get("ewidencja")),
        NEW_MACHINE_ID: (TARGET_CLIENT_ID, NEW_BOUND_EWIDENCJA),
    }
    for machine_id, (client_id, ewidencja) in expected_machines.items():
        row = machines[machine_id]
        if int(row.get("id_klient") or 0) != client_id:
            raise Form70ReplacementError(f"MASZYNA {machine_id} ma nieoczekiwanego klienta.")
        if machine_id != KEPT_MACHINE_ID and _normalize(row.get("ewidencja")) != _normalize(
            ewidencja
        ):
            raise Form70ReplacementError(f"MASZYNA {machine_id} ma nieoczekiwaną ewidencję.")

    sheet_rows = state["sheet"]["rows"]
    old_sheet = sheet_rows[str(OLD_SOURCE_ROW)]
    if old_sheet.get("reservation_status") != WORKFLOW_SERVICE_BLOCK_STATUS:
        raise Form70ReplacementError("Stare urządzenie nie ma blokady serwisowej w arkuszu.")
    if _normalize(SERVICE_NOTE) not in _normalize(old_sheet.get("notes")):
        raise Form70ReplacementError("Stare urządzenie nie ma uwagi serwisowej w arkuszu.")
    if any(
        str(old_sheet.get(key) or "").strip()
        for key in (
            "reservation_grenke",
            "form_ctip",
            "proforma_grenke",
            "ctip_form_id",
            "ctip_workflow_case_id",
        )
    ):
        raise Form70ReplacementError("Stare urządzenie nadal ma dane FLOW w arkuszu.")
    for source_row in (KEPT_SOURCE_ROW, NEW_SOURCE_ROW):
        row = sheet_rows[str(source_row)]
        if row.get("reservation_status") != WORKFLOW_RESERVATION_STATUS:
            raise Form70ReplacementError(f"Urządzenie {source_row} nie ma rezerwacji GRENKE.")
        if row.get("proforma_grenke") != new_proforma["number"]:
            raise Form70ReplacementError(f"Urządzenie {source_row} nie ma nowej proformy.")
        if str(row.get("ctip_form_id") or "") != str(FORM_ID):
            raise Form70ReplacementError(f"Urządzenie {source_row} nie wskazuje formularza 70.")

    old_pdf = journal["old_pdf"]
    source = Path(old_pdf["source"])
    if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != old_pdf["sha256"]:
        raise Form70ReplacementError("Stary PDF proformy został zmieniony.")
    new_pdf = build_proforma_pdf_storage_path(int(new_proforma["id"]))
    if not new_pdf.is_file() or new_pdf.stat().st_size <= 0:
        raise Form70ReplacementError("Brak poprawnego PDF nowej proformy.")


def _restore_machines() -> dict[str, Any]:
    connection = _firebird_connection()
    cursor = connection.cursor()
    try:
        transitions = (
            (OLD_MACHINE_ID, WAREHOUSE_CLIENT_ID, OLD_INDEX, TARGET_CLIENT_ID, OLD_BOUND_EWIDENCJA),
            (
                NEW_MACHINE_ID,
                TARGET_CLIENT_ID,
                NEW_BOUND_EWIDENCJA,
                WAREHOUSE_CLIENT_ID,
                NEW_WAREHOUSE_EWIDENCJA,
            ),
        )
        changed = []
        for machine_id, source_client, source_index, target_client, target_index in transitions:
            rows = _fetch_rows(
                cursor,
                "SELECT ID_MASZYNA, ID_KLIENT, EWIDENCJA FROM MASZYNA WHERE ID_MASZYNA = ?",
                (machine_id,),
            )
            if len(rows) != 1:
                raise Form70ReplacementError(f"Rollback: brak MASZYNA {machine_id}.")
            row = rows[0]
            current = (int(row.get("id_klient") or 0), _normalize(row.get("ewidencja")))
            source = (source_client, _normalize(source_index))
            target = (target_client, _normalize(target_index))
            if current == target:
                continue
            if current != source:
                raise Form70ReplacementError(f"Rollback: MASZYNA {machine_id} ma obcy stan.")
            cursor.execute(
                "UPDATE MASZYNA SET ID_KLIENT = ?, EWIDENCJA = ? WHERE ID_MASZYNA = ?",
                (target_client, target_index, machine_id),
            )
            changed.append(machine_id)
        connection.commit()
        return {"changed_machine_ids": changed}
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


async def _restore_postgres(session: AsyncSession, journal: dict[str, Any]) -> dict[str, Any]:
    workflow_case = await session.get(FormWorkflowCase, CASE_ID)
    if workflow_case is None:
        raise Form70ReplacementError("Rollback: brak sprawy workflow 40.")
    before = journal["before"]["postgres"]
    restored = await replace_form_workflow_devices(
        session,
        workflow_case=workflow_case,
        selected_devices=[_device_restore_payload(item) for item in before["devices"]],
        updated_by=journal.get("actor_id"),
    )
    before_case = before["case"]
    workflow_case.proforma_firebird_id = before_case.get("proforma_firebird_id")
    workflow_case.proforma_number = before_case.get("proforma_number")
    workflow_case.proforma_pdf_path = before_case.get("proforma_pdf_path")
    workflow_case.stage = before_case.get("stage")
    workflow_case.business_status = before_case.get("business_status")
    workflow_case.status_source = before_case.get("status_source")
    workflow_case.status_changed_at = _parse_datetime(before_case.get("status_changed_at"))
    workflow_case.status_history = deepcopy(before_case.get("status_history") or [])
    workflow_case.signature_deadline_at = _parse_datetime(before_case.get("signature_deadline_at"))
    workflow_case.resources_release_due_at = _parse_datetime(
        before_case.get("resources_release_due_at")
    )
    workflow_case.resources_released_at = _parse_datetime(before_case.get("resources_released_at"))
    workflow_case.updated_by = before_case.get("updated_by")
    workflow_case.updated_at = _parse_datetime(before_case.get("updated_at")) or datetime.now(UTC)
    await record_audit(
        session,
        user_id=journal.get("actor_id"),
        action="contracts_flow_device_replacement_rollback",
        client_ip=None,
        payload={
            "operation_key": OPERATION_KEY,
            "run_id": journal["run_id"],
            "form_request_id": FORM_ID,
            "workflow_case_id": CASE_ID,
        },
    )
    await session.commit()
    return {"device_ids": [item.id for item in restored]}


async def _restore_sheet(session: AsyncSession, journal: dict[str, Any]) -> dict[str, Any]:
    before_rows = journal["before"]["sheet"]["rows"]
    current = await _load_sheet_state(session)
    changes = []
    for source_row, serial, index in (
        (OLD_SOURCE_ROW, OLD_SERIAL, OLD_INDEX),
        (KEPT_SOURCE_ROW, KEPT_SERIAL, KEPT_INDEX),
        (NEW_SOURCE_ROW, NEW_SERIAL, NEW_INDEX),
    ):
        before = before_rows[str(source_row)]
        actual = current["rows"][str(source_row)]
        changes.append(
            {
                "device": {
                    "source_row": source_row,
                    "sheet_row": actual.get("sheet_row"),
                    "serial": serial,
                    "index": index,
                },
                "expected_fields": {key: actual.get(key) or "" for key in SHEET_WORKFLOW_FIELDS},
                "target_fields": {key: before.get(key) or "" for key in SHEET_WORKFLOW_FIELDS},
                "background": "default" if source_row == NEW_SOURCE_ROW else "reserved",
            }
        )
    config = await load_workflow_sheet_runtime_config(session)
    with use_workflow_sheet_runtime_config(config):
        result = await asyncio.to_thread(
            update_workflow_sheet_fields_for_maintenance,
            changes=changes,
        )
    cache_result = await refresh_workflow_sheet_status_cache(
        session, user_id=journal.get("actor_id")
    )
    if not cache_result.get("success"):
        raise Form70ReplacementError("Rollback: nie odświeżono cache arkusza.")
    return {"sheet": result, "cache": cache_result}


async def _apply(args: argparse.Namespace) -> dict[str, Any]:
    _assert_production_target(require_writes=True)
    backup_dir = _assert_backup(args.backup_dir)
    if args.confirmation != APPLY_CONFIRMATION:
        raise Form70ReplacementError(
            f"Niepoprawna fraza potwierdzająca. Wymagana: {APPLY_CONFIRMATION}"
        )
    if not args.state_token:
        raise Form70ReplacementError("Tryb --apply wymaga tokenu z aktualnego dry-run.")

    args.report_dir.mkdir(parents=True, exist_ok=True)
    journal_path = _journal_path(args.report_dir)
    async with AsyncSessionLocal() as session:
        if journal_path.is_file():
            journal = _load_journal(args.report_dir)
            if journal.get("phase") == "completed":
                state = await _load_state(
                    session,
                    planned_proforma_number=journal["planned_proforma_number"],
                )
                _validate_completed_state(state, journal)
                return {"mode": "apply", "resumed": True, **journal}
        else:
            before = await _load_state(session)
            validate_initial_state(before)
            actual_token = _state_token(before)
            if actual_token != args.state_token:
                raise Form70ReplacementError(
                    "Stan zmienił się od dry-run. Wykonaj ponownie dry-run i użyj nowego tokenu."
                )
            old_sheet = before["sheet"]["rows"][str(OLD_SOURCE_ROW)]
            kept_sheet = before["sheet"]["rows"][str(KEPT_SOURCE_ROW)]
            reservation_label = str(
                kept_sheet.get("reservation_grenke") or old_sheet.get("reservation_grenke") or ""
            ).strip()
            if not reservation_label:
                raise Form70ReplacementError("Brak etykiety rezerwacji GRENKE do zachowania.")
            business_label = str(
                kept_sheet.get("business_status_legacy")
                or old_sheet.get("business_status_legacy")
                or "Umowa zaakceptowana"
            ).strip()
            run_id = str(uuid4())
            journal = {
                "mode": "apply",
                "operation_key": OPERATION_KEY,
                "run_id": run_id,
                "created_at": datetime.now(UTC),
                "phase": "prepared",
                "state_token": actual_token,
                "backup_dir": str(backup_dir),
                "planned_proforma_number": before["firebird"]["planned_proforma_number"],
                "actor_id": before["postgres"].get("actor_id"),
                "sheet_reservation_label": reservation_label,
                "sheet_business_status_label": business_label,
                "before": before,
            }
            _save_journal(args.report_dir, journal, phase="prepared")
            journal["old_pdf"] = await asyncio.to_thread(_copy_old_pdf, args.report_dir)
            _save_journal(args.report_dir, journal, phase="old_pdf_protected")

        if "new_proforma" not in journal:
            journal["new_proforma"] = await asyncio.to_thread(_ensure_new_proforma, journal)
            _save_journal(args.report_dir, journal, phase="proforma_created")
        journal["machines"] = await asyncio.to_thread(_apply_machine_replacement)
        _save_journal(args.report_dir, journal, phase="machines_replaced")
        journal["postgres"] = await _apply_postgres_replacement(session, journal=journal)
        _save_journal(args.report_dir, journal, phase="postgres_replaced")
        journal["sheet"] = await _apply_sheet_replacement(session, journal=journal)
        _save_journal(args.report_dir, journal, phase="sheet_replaced")
        completed = await _load_state(
            session,
            planned_proforma_number=journal["planned_proforma_number"],
        )
        _validate_completed_state(completed, journal)
        journal["after"] = completed
        _save_journal(args.report_dir, journal, phase="completed")
        return {"mode": "apply", "resumed": False, **journal}


async def _rollback(args: argparse.Namespace) -> dict[str, Any]:
    _assert_production_target(require_writes=True)
    journal = _load_journal(args.report_dir, run_id=args.rollback)
    required = f"WYCOFAJ ZAMIANE FORMULARZA 70 {journal['run_id']}"
    if args.confirmation != required:
        raise Form70ReplacementError(f"Niepoprawna fraza potwierdzająca. Wymagana: {required}")
    _assert_backup(Path(journal["backup_dir"]))
    if "new_proforma" not in journal:
        raise Form70ReplacementError("Dziennik nie zawiera nowej proformy do rollbacku.")

    async with AsyncSessionLocal() as session:
        sheet_result = await _restore_sheet(session, journal)
        postgres_result = await _restore_postgres(session, journal)
        machine_result = await asyncio.to_thread(_restore_machines)
        new_proforma_id = int(journal["new_proforma"]["id"])
        delete_result = await asyncio.to_thread(delete_proforma_from_firebird, new_proforma_id)
        if not delete_result.deleted:
            current = _load_firebird_state(
                planned_proforma_number=journal["planned_proforma_number"]
            )
            if current.get("planned_proforma") is not None:
                raise Form70ReplacementError("Rollback nie usunął nowej proformy.")
        result = {
            "mode": "rollback",
            "operation_key": OPERATION_KEY,
            "run_id": journal["run_id"],
            "sheet": sheet_result,
            "postgres": postgres_result,
            "machines": machine_result,
            "deleted_new_proforma": _json_safe(asdict(delete_result)),
        }
        journal["rollback"] = result
        _save_journal(args.report_dir, journal, phase="rolled_back")
        return result


async def _dry_run(args: argparse.Namespace) -> dict[str, Any]:
    _assert_production_target()
    async with AsyncSessionLocal() as session:
        state = await _load_state(session)
        validate_initial_state(state)
        await session.rollback()
    return {
        "mode": "dry-run",
        "operation_key": OPERATION_KEY,
        "state_token": _state_token(state),
        "required_confirmation": APPLY_CONFIRMATION,
        "summary": _public_summary(state),
    }


async def execute(args: argparse.Namespace) -> dict[str, Any]:
    """Wykonuje dry-run, zapis albo kontrolowany rollback korekty."""
    if args.rollback:
        return await _rollback(args)
    if args.apply:
        return await _apply(args)
    return await _dry_run(args)


async def _run(args: argparse.Namespace) -> int:
    try:
        payload = await execute(args)
        report_path = _write_report(args.report_dir, payload)
        public_payload = {
            key: value
            for key, value in payload.items()
            if key not in {"before", "after", "old_pdf"}
        }
        public_payload["report_path"] = str(report_path)
        print(json.dumps(_json_safe(public_payload), ensure_ascii=False, indent=2))
        return 0
    finally:
        await engine.dispose()


def main() -> int:
    """Uruchamia skrypt z obsługą pętli zdarzeń na Windows i Linux."""
    from app.core.asyncio_compat import configure_asyncio_for_windows

    configure_asyncio_for_windows()
    try:
        return asyncio.run(_run(parse_args()))
    except (Form70ReplacementError, RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    raise SystemExit(main())
