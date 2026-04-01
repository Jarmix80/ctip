"""Logika dashboardu obslugi umow (formularze + Firebird + Google Sheets)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import FormRequest


def normalize_nip(value: str | None) -> str:
    """Normalizuje NIP do postaci cyfr."""
    if not value:
        return ""
    return re.sub(r"\D+", "", value)


def normalize_device_key(value: str | None) -> str:
    """Normalizuje klucze urzadzen (serial/ewidencja)."""
    if not value:
        return ""
    return re.sub(r"[^A-Z0-9]", "", value.upper())


@dataclass(slots=True)
class FirebirdClientMatch:
    found: bool
    id_klient: int | None = None
    nazwa: str | None = None
    nip: str | None = None
    telefon: str | None = None
    email: str | None = None
    error: str | None = None


@dataclass(slots=True)
class FirebirdClientWriteResult:
    created: bool
    match: FirebirdClientMatch


@dataclass(slots=True)
class FirebirdModelMatch:
    found: bool
    id_model: int | None = None
    marka: str | None = None
    model: str | None = None
    error: str | None = None


@dataclass(slots=True)
class FirebirdWarehouseMatch:
    found: bool
    id_magazyn_table: int | None = None
    indeks: str | None = None
    nazwa: str | None = None
    error: str | None = None


@dataclass(slots=True)
class FirebirdDeviceSyncResult:
    row: int
    serial: str | None
    ewidencja: str | None
    model_source: str | None
    model_id: int | None
    machine_id: int | None
    machine_created: bool
    warehouse_id: int | None
    warehouse_created: bool


@dataclass(slots=True)
class DeviceMatch:
    source_row: int
    serial: str | None
    ewidencja: str | None
    model: str | None
    found_in_firebird: bool
    id_maszyna: int | None
    id_klient: int | None
    id_umowacpc: int | None
    error: str | None = None


def _extract_submitted_payload(item: FormRequest) -> dict[str, Any] | None:
    from app.services import form_generator

    if item.status != "SUBMITTED":
        return None
    payload, _ = form_generator.decode_submitted_payload(item)
    if payload and isinstance(payload, dict):
        return payload
    return None


async def load_submitted_forms(session: AsyncSession, *, limit: int = 250) -> list[FormRequest]:
    """Pobiera formularze i filtruje tylko wypelnione."""
    return await load_contract_forms(session, limit=limit, submitted_only=True)


async def load_contract_forms(
    session: AsyncSession,
    *,
    limit: int = 250,
    submitted_only: bool = False,
) -> list[FormRequest]:
    """Pobiera formularze workflow, opcjonalnie tylko te wypelnione."""
    from app.services import form_generator

    items = await form_generator.list_form_requests(session, limit=limit)
    if submitted_only:
        return [item for item in items if item.status == "SUBMITTED"]
    return items


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_local_firebird_path() -> Path:
    db_path = Path(settings.fb_local_copy_path)
    if not db_path.is_absolute():
        db_path = _repo_root() / db_path
    return db_path


def firebird_writes_enabled() -> tuple[bool, str | None]:
    """Sprawdza, czy zapis do lokalnej kopii Firebird jest jawnie odblokowany."""
    if not settings.fb_allow_writes:
        return (
            False,
            "Zapis do lokalnej Firebird jest zablokowany. Ustaw FB_ALLOW_WRITES=true w srodowisku testowym.",
        )

    db_path = _resolve_local_firebird_path()
    if not db_path.exists():
        return False, f"Brak lokalnej kopii Firebird do zapisu: {db_path}"

    try:
        db_path.resolve().relative_to(_repo_root().resolve())
    except ValueError:
        return False, f"Zapis jest dozwolony tylko do lokalnej kopii w repozytorium: {db_path}"

    return True, None


def _firebird_connection():
    import firebirdsql  # type: ignore[import-not-found]

    fb_port = settings.fb_port
    db_path = _resolve_local_firebird_path()
    if not db_path.exists():
        raise FileNotFoundError(f"Brak lokalnej kopii Firebird: {db_path}")

    return firebirdsql.connect(
        host="127.0.0.1",
        port=fb_port,
        database=str(db_path),
        user=settings.fb_user,
        password=settings.fb_password,
        charset=settings.fb_charset,
    )


def _truncate_text(value: str | None, max_length: int) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    return normalized[:max_length]


def _build_address(payload: dict[str, Any]) -> str | None:
    street = _truncate_text(str(payload.get("registered_street") or ""), 160)
    building = _truncate_text(str(payload.get("registered_building_no") or ""), 20)
    apartment = _truncate_text(str(payload.get("registered_apartment_no") or ""), 20)
    if not street:
        return None

    suffix = building or ""
    if apartment:
        suffix = f"{suffix}/{apartment}" if suffix else apartment
    if suffix:
        return _truncate_text(f"{street} {suffix}", 250)
    return _truncate_text(street, 250)


def _build_notes(payload: dict[str, Any], *, source_name: str | None) -> str | None:
    parts: list[str] = []
    if source_name:
        parts.append(source_name.strip())
    billing_email = _truncate_text(str(payload.get("billing_email") or ""), 200)
    if billing_email:
        parts.append(f"e-faktura: {billing_email}")
    return _truncate_text(" | ".join(parts), 1000)


def _build_device_note(*, row: int) -> str:
    return f"CTIP arkusz Urzadzenia row {row}"


def _build_stock_name(
    *,
    serial: str | None,
    ewidencja: str | None,
    model_name: str | None,
    marka: str | None,
) -> str:
    parts: list[str] = []
    if marka:
        parts.append(marka.strip())
    if model_name:
        parts.append(model_name.strip())
    base = " ".join(part for part in parts if part).strip() or (model_name or "Urzadzenie").strip()
    details: list[str] = []
    if serial:
        details.append(f"S/N:{serial.strip()}")
    if ewidencja:
        details.append(f"nr.wew: {ewidencja.strip()}")
    if details:
        return _truncate_text(f"{base} {', '.join(details)}", 250) or "Urzadzenie"
    return _truncate_text(base, 250) or "Urzadzenie"


def create_client_from_submitted_payload(
    payload: dict[str, Any],
    *,
    source_name: str | None = None,
    kto: str = "CTIP",
) -> FirebirdClientWriteResult:
    """Tworzy klienta w lokalnej kopii Firebird na podstawie formularza SUBMITTED."""
    enabled, reason = firebird_writes_enabled()
    if not enabled:
        raise RuntimeError(reason or "Zapis do Firebird jest zablokowany.")

    company_name = _truncate_text(str(payload.get("company_name") or ""), 500)
    if not company_name:
        raise ValueError("Formularz nie zawiera nazwy klienta.")

    nip = normalize_nip(str(payload.get("company_nip") or ""))
    if not nip:
        raise ValueError("Formularz nie zawiera NIP klienta.")

    existing = find_client_in_firebird(nip)
    if existing.error:
        raise RuntimeError(f"Nie udalo sie zweryfikowac klienta w Firebird: {existing.error}")
    if existing.found:
        return FirebirdClientWriteResult(created=False, match=existing)

    connection = _firebird_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO KLIENT (
                ID_ODDZIAL,
                ID_FIRMA,
                NAZWA,
                ADRES,
                KOD,
                POCZTA,
                NIP,
                TELEFON,
                E_MAIL,
                UWAGI,
                AKTYWNY,
                KTO
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                0,
                company_name,
                _build_address(payload),
                _truncate_text(str(payload.get("registered_postal_code") or ""), 6),
                _truncate_text(str(payload.get("registered_city") or ""), 150),
                nip,
                _truncate_text(str(payload.get("company_phone") or ""), 100),
                _truncate_text(str(payload.get("company_email") or ""), 200),
                _build_notes(payload, source_name=source_name),
                "TAK",
                _truncate_text(kto, 50),
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()

    created = find_client_in_firebird(nip)
    if created.error:
        raise RuntimeError(f"Klient zapisany, ale odczyt po zapisie nie udal sie: {created.error}")
    if not created.found or created.id_klient is None:
        raise RuntimeError("Klient zostal zapisany, ale nie udalo sie ustalic jego ID w Firebird.")
    return FirebirdClientWriteResult(created=True, match=created)


def find_model_in_firebird(model_name: str | None) -> FirebirdModelMatch:
    """Wyszukuje model po nazwie tekstowej z arkusza."""
    normalized = _truncate_text(model_name, 50)
    if not normalized:
        return FirebirdModelMatch(found=False)

    try:
        connection = _firebird_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT FIRST 1 ID_MODEL, MARKA, MODEL
                FROM MODEL
                WHERE UPPER(TRIM(MODEL)) = UPPER(TRIM(?))
                   OR UPPER(TRIM(MARKA || ' ' || MODEL)) = UPPER(TRIM(?))
                ORDER BY ID_MODEL DESC
                """,
                (normalized, normalized),
            )
            row = cursor.fetchone()
            if row:
                return FirebirdModelMatch(
                    found=True,
                    id_model=int(row[0]) if row[0] is not None else None,
                    marka=_truncate_text(str(row[1]) if row[1] is not None else "", 50),
                    model=_truncate_text(str(row[2]) if row[2] is not None else "", 50),
                )

            cursor.execute(
                """
                SELECT FIRST 1 ID_MODEL, MARKA, MODEL
                FROM MODEL
                WHERE UPPER(TRIM(MODEL)) CONTAINING UPPER(TRIM(?))
                ORDER BY ID_MODEL DESC
                """,
                (normalized,),
            )
            row = cursor.fetchone()
            if not row:
                return FirebirdModelMatch(found=False)
            return FirebirdModelMatch(
                found=True,
                id_model=int(row[0]) if row[0] is not None else None,
                marka=_truncate_text(str(row[1]) if row[1] is not None else "", 50),
                model=_truncate_text(str(row[2]) if row[2] is not None else "", 50),
            )
        finally:
            cursor.close()
            connection.close()
    except Exception as exc:  # noqa: BLE001
        return FirebirdModelMatch(found=False, error=str(exc))


def find_warehouse_item_in_firebird(ewidencja: str | None) -> FirebirdWarehouseMatch:
    """Wyszukuje pozycje magazynowa po indeksie / ewidencji."""
    normalized = normalize_device_key(ewidencja)
    if not normalized:
        return FirebirdWarehouseMatch(found=False)

    try:
        connection = _firebird_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT FIRST 1 ID_MAGAZYN_TABLE, INDEKS, NAZWA
                FROM MAGAZYN
                WHERE UPPER(REPLACE(REPLACE(REPLACE(INDEKS, '/', ''), '-', ''), ' ', '')) = ?
                ORDER BY ID_MAGAZYN_TABLE DESC
                """,
                (normalized,),
            )
            row = cursor.fetchone()
            if not row:
                return FirebirdWarehouseMatch(found=False)
            return FirebirdWarehouseMatch(
                found=True,
                id_magazyn_table=int(row[0]) if row[0] is not None else None,
                indeks=_truncate_text(str(row[1]) if row[1] is not None else "", 100),
                nazwa=_truncate_text(str(row[2]) if row[2] is not None else "", 250),
            )
        finally:
            cursor.close()
            connection.close()
    except Exception as exc:  # noqa: BLE001
        return FirebirdWarehouseMatch(found=False, error=str(exc))


def load_device_from_sheet_row(row: int) -> dict[str, str] | None:
    """Zwraca pojedynczy rekord urzadzenia z arkusza po numerze wiersza."""
    for item in load_devices_from_sheet():
        try:
            current_row = int(item.get("row") or 0)
        except ValueError:
            continue
        if current_row == row:
            return item
    return None


def synchronize_device_from_sheet_row(
    row: int,
    *,
    kto: str = "CTIP",
) -> FirebirdDeviceSyncResult:
    """Synchronizuje urzadzenie z arkusza do lokalnej Firebird (MASZYNA + MAGAZYN)."""
    enabled, reason = firebird_writes_enabled()
    if not enabled:
        raise RuntimeError(reason or "Zapis do Firebird jest zablokowany.")

    device = load_device_from_sheet_row(row)
    if device is None:
        raise ValueError(f"Nie znaleziono wiersza {row} w arkuszu Urzadzenia.")

    serial = _truncate_text(device.get("serial"), 100)
    ewidencja = _truncate_text(device.get("ewidencja"), 100)
    model_source = _truncate_text(device.get("model"), 50)
    if not serial and not ewidencja:
        raise ValueError(f"Wiersz {row} nie zawiera serialu ani ewidencji.")

    machine = find_device_in_firebird(serial, ewidencja)
    if machine.error:
        raise RuntimeError(f"Nie udalo sie sprawdzic urzadzenia w Firebird: {machine.error}")
    warehouse = find_warehouse_item_in_firebird(ewidencja)
    if warehouse.error:
        raise RuntimeError(f"Nie udalo sie sprawdzic pozycji magazynowej: {warehouse.error}")
    model_match = find_model_in_firebird(model_source)
    if model_match.error:
        raise RuntimeError(f"Nie udalo sie sprawdzic modelu w Firebird: {model_match.error}")

    if machine.found_in_firebird and warehouse.found:
        return FirebirdDeviceSyncResult(
            row=row,
            serial=serial,
            ewidencja=ewidencja,
            model_source=model_source,
            model_id=model_match.id_model,
            machine_id=machine.id_maszyna,
            machine_created=False,
            warehouse_id=warehouse.id_magazyn_table,
            warehouse_created=False,
        )

    connection = _firebird_connection()
    cursor = connection.cursor()
    try:
        if not machine.found_in_firebird:
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
                """,
                (
                    1,
                    1,
                    settings.fb_warehouse_client_id,
                    model_match.id_model,
                    _truncate_text(model_match.marka, 100),
                    _truncate_text(model_match.model or model_source, 100),
                    serial,
                    ewidencja,
                    "TAK",
                    _build_device_note(row=row),
                ),
            )

        if not warehouse.found:
            stock_index = ewidencja or serial
            cursor.execute(
                """
                INSERT INTO MAGAZYN (
                    ID_ODDZIAL,
                    ID_FIRMA,
                    ID_MAGAZYN,
                    INDEKS,
                    NAZWA,
                    JM,
                    ILOSC,
                    DATA_Z,
                    MARKA,
                    MODEL,
                    ID_MODEL,
                    SERIAL,
                    IDVAT,
                    VAT_STAWKA,
                    UWAGI
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    1,
                    1,
                    settings.fb_warehouse_id,
                    _truncate_text(stock_index, 100),
                    _build_stock_name(
                        serial=serial,
                        ewidencja=ewidencja,
                        model_name=model_match.model or model_source,
                        marka=model_match.marka,
                    ),
                    "szt.",
                    1,
                    date.today(),
                    _truncate_text(model_match.marka, 50),
                    _truncate_text(model_match.model or model_source, 50),
                    model_match.id_model,
                    "NIE",
                    1,
                    "23 %",
                    _build_device_note(row=row),
                ),
            )

        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()

    machine_after = find_device_in_firebird(serial, ewidencja)
    warehouse_after = find_warehouse_item_in_firebird(ewidencja)
    if machine_after.error:
        raise RuntimeError(
            f"Urzadzenie zapisane, ale odczyt po zapisie nie udal sie: {machine_after.error}"
        )
    if warehouse_after.error:
        raise RuntimeError(
            f"Pozycja magazynowa zapisana, ale odczyt po zapisie nie udal sie: {warehouse_after.error}"
        )

    return FirebirdDeviceSyncResult(
        row=row,
        serial=serial,
        ewidencja=ewidencja,
        model_source=model_source,
        model_id=model_match.id_model,
        machine_id=machine_after.id_maszyna,
        machine_created=not machine.found_in_firebird,
        warehouse_id=warehouse_after.id_magazyn_table,
        warehouse_created=not warehouse.found,
    )


def find_client_in_firebird(nip: str) -> FirebirdClientMatch:
    """Wyszukuje klienta po NIP w lokalnej kopii Firebird."""
    cleaned = normalize_nip(nip)
    if not cleaned:
        return FirebirdClientMatch(found=False)
    try:
        connection = _firebird_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT FIRST 1 ID_KLIENT, NAZWA, NIP, TELEFON, E_MAIL
                FROM KLIENT
                WHERE REPLACE(
                    REPLACE(
                        REPLACE(
                            REPLACE(REPLACE(UPPER(COALESCE(NIP, '')), 'PL', ''), '-', ''),
                            ' ',
                            ''
                        ),
                        '.',
                        ''
                    ),
                    '/',
                    ''
                ) = ?
                """,
                (cleaned,),
            )
            row = cursor.fetchone()
            if row:
                return FirebirdClientMatch(
                    found=True,
                    id_klient=int(row[0]) if row[0] is not None else None,
                    nazwa=str(row[1]) if row[1] is not None else None,
                    nip=str(row[2]) if row[2] is not None else None,
                    telefon=str(row[3]) if row[3] is not None else None,
                    email=str(row[4]) if row[4] is not None else None,
                )

            cursor.execute(
                """
                SELECT FIRST 1 ID_KLIENT, NAZWA, NIP, TELEFON, E_MAIL
                FROM KLIENT
                WHERE REPLACE(
                    REPLACE(
                        REPLACE(
                            REPLACE(REPLACE(UPPER(COALESCE(NIP, '')), 'PL', ''), '-', ''),
                            ' ',
                            ''
                        ),
                        '.',
                        ''
                    ),
                    '/',
                    ''
                ) CONTAINING ?
                ORDER BY ID_KLIENT ASC
                """,
                (cleaned,),
            )
            row = cursor.fetchone()
            if not row:
                return FirebirdClientMatch(found=False)
            return FirebirdClientMatch(
                found=True,
                id_klient=int(row[0]) if row[0] is not None else None,
                nazwa=str(row[1]) if row[1] is not None else None,
                nip=str(row[2]) if row[2] is not None else None,
                telefon=str(row[3]) if row[3] is not None else None,
                email=str(row[4]) if row[4] is not None else None,
            )
        finally:
            cursor.close()
            connection.close()
    except Exception as exc:  # noqa: BLE001
        return FirebirdClientMatch(found=False, error=str(exc))


def find_client_in_firebird_by_id(client_id: int | None) -> FirebirdClientMatch:
    """Wyszukuje klienta po ID_KLIENT w lokalnej kopii Firebird."""
    if not client_id:
        return FirebirdClientMatch(found=False)

    try:
        connection = _firebird_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT FIRST 1 ID_KLIENT, NAZWA, NIP, TELEFON, E_MAIL
                FROM KLIENT
                WHERE ID_KLIENT = ?
                """,
                (int(client_id),),
            )
            row = cursor.fetchone()
            if not row:
                return FirebirdClientMatch(found=False)
            return FirebirdClientMatch(
                found=True,
                id_klient=int(row[0]) if row[0] is not None else None,
                nazwa=str(row[1]) if row[1] is not None else None,
                nip=str(row[2]) if row[2] is not None else None,
                telefon=str(row[3]) if row[3] is not None else None,
                email=str(row[4]) if row[4] is not None else None,
            )
        finally:
            cursor.close()
            connection.close()
    except Exception as exc:  # noqa: BLE001
        return FirebirdClientMatch(found=False, error=str(exc))


def _sheet_headers_map(headers: list[str]) -> dict[str, int]:
    mapped: dict[str, int] = {}
    for idx, col in enumerate(headers):
        normalized = col.strip().lower()
        mapped[normalized] = idx
    return mapped


def load_devices_from_sheet() -> list[dict[str, str]]:
    """Pobiera urzadzenia z arkusza Urzadzenia."""
    credentials_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    spreadsheet_id = os.environ.get("GOOGLE_SHEETS_SPREADSHEET_ID")
    if not credentials_path or not spreadsheet_id:
        return []

    import gspread
    from google.oauth2.service_account import Credentials

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
    ]
    credentials = Credentials.from_service_account_file(credentials_path, scopes=scopes)
    client = gspread.authorize(credentials)
    workbook = client.open_by_key(spreadsheet_id)
    worksheet = workbook.worksheet("Urzadzenia")
    values = worksheet.get_all_values()
    if len(values) < 2:
        return []
    headers = _sheet_headers_map(values[0])

    producer_idx = headers.get("producent", 0)
    serial_idx = headers.get("serial", headers.get("sn", 2))
    ewidencja_idx = headers.get("ewidencja", headers.get("nr wewnetrzny", 3))
    model_idx = headers.get("model", 1)
    status_idx = headers.get("status")
    price_idx = headers.get("cena")
    reservation_idx = headers.get("rezerwacja")
    reservation_status_idx = headers.get("status rezerwacji")
    description_idx = headers.get("opis")
    ms_id_maszyna_idx = headers.get("ms_id_maszyna")
    ms_id_klient_idx = headers.get("ms_id_klient")
    ms_nazwa_klienta_idx = headers.get("ms_nazwa_klienta")
    ms_nip_idx = headers.get("ms_nip")

    def _cell(row: list[str], idx: int | None) -> str:
        if idx is None or idx >= len(row):
            return ""
        return row[idx].strip()

    output: list[dict[str, str]] = []
    for row_no, row in enumerate(values[1:], start=2):
        if not any((cell or "").strip() for cell in row):
            continue
        serial = _cell(row, serial_idx)
        ewidencja = _cell(row, ewidencja_idx)
        model = _cell(row, model_idx)
        output.append(
            {
                "row": str(row_no),
                "producer": _cell(row, producer_idx),
                "serial": serial,
                "ewidencja": ewidencja,
                "model": model,
                "status": _cell(row, status_idx),
                "price": _cell(row, price_idx),
                "reservation": _cell(row, reservation_idx),
                "reservation_status": _cell(row, reservation_status_idx),
                "description": _cell(row, description_idx),
                "ms_id_maszyna": _cell(row, ms_id_maszyna_idx),
                "ms_id_klient": _cell(row, ms_id_klient_idx),
                "ms_nazwa_klienta": _cell(row, ms_nazwa_klienta_idx),
                "ms_nip": _cell(row, ms_nip_idx),
            }
        )
    return output


def find_device_in_firebird(serial: str | None, ewidencja: str | None) -> DeviceMatch:
    """Wyszukuje urzadzenie po SERIAL/SERIAL2/EWIDENCJA."""
    serial_value = normalize_device_key(serial)
    ewidencja_value = normalize_device_key(ewidencja)
    if not serial_value and not ewidencja_value:
        return DeviceMatch(
            source_row=0,
            serial=serial,
            ewidencja=ewidencja,
            model=None,
            found_in_firebird=False,
            id_maszyna=None,
            id_klient=None,
            id_umowacpc=None,
            error=None,
        )

    try:
        connection = _firebird_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT FIRST 1 ID_MASZYNA, SERIAL, SERIAL2, EWIDENCJA, ID_KLIENT, ID_UMOWACPC
                FROM MASZYNA
                WHERE UPPER(REPLACE(REPLACE(REPLACE(SERIAL, '/', ''), '-', ''), ' ', '')) = ?
                   OR UPPER(REPLACE(REPLACE(REPLACE(SERIAL2, '/', ''), '-', ''), ' ', '')) = ?
                   OR UPPER(REPLACE(REPLACE(REPLACE(EWIDENCJA, '/', ''), '-', ''), ' ', '')) = ?
                """,
                (
                    serial_value or "___NO_MATCH___",
                    serial_value or "___NO_MATCH___",
                    ewidencja_value or "___NO_MATCH___",
                ),
            )
            row = cursor.fetchone()
            if not row:
                return DeviceMatch(
                    source_row=0,
                    serial=serial,
                    ewidencja=ewidencja,
                    model=None,
                    found_in_firebird=False,
                    id_maszyna=None,
                    id_klient=None,
                    id_umowacpc=None,
                    error=None,
                )
            return DeviceMatch(
                source_row=0,
                serial=str(row[1]) if row[1] is not None else serial,
                ewidencja=str(row[3]) if row[3] is not None else ewidencja,
                model=None,
                found_in_firebird=True,
                id_maszyna=int(row[0]) if row[0] is not None else None,
                id_klient=int(row[4]) if row[4] is not None else None,
                id_umowacpc=int(row[5]) if row[5] is not None else None,
                error=None,
            )
        finally:
            cursor.close()
            connection.close()
    except Exception as exc:  # noqa: BLE001
        return DeviceMatch(
            source_row=0,
            serial=serial,
            ewidencja=ewidencja,
            model=None,
            found_in_firebird=False,
            id_maszyna=None,
            id_klient=None,
            id_umowacpc=None,
            error=str(exc),
        )


__all__ = [
    "DeviceMatch",
    "FirebirdClientWriteResult",
    "FirebirdClientMatch",
    "FirebirdDeviceSyncResult",
    "FirebirdModelMatch",
    "FirebirdWarehouseMatch",
    "create_client_from_submitted_payload",
    "find_model_in_firebird",
    "find_warehouse_item_in_firebird",
    "find_client_in_firebird",
    "find_client_in_firebird_by_id",
    "find_device_in_firebird",
    "firebird_writes_enabled",
    "load_contract_forms",
    "load_device_from_sheet_row",
    "load_devices_from_sheet",
    "load_submitted_forms",
    "normalize_device_key",
    "normalize_nip",
    "synchronize_device_from_sheet_row",
]
