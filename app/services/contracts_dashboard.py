"""Logika dashboardu obslugi umow (formularze + Firebird + Google Sheets)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

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
    from app.services import form_generator

    items = await form_generator.list_form_requests(session, limit=limit)
    return [item for item in items if item.status == "SUBMITTED"]


def _firebird_connection():
    import firebirdsql  # type: ignore[import-not-found]

    fb_port = int(os.environ.get("FB_PORT", "3050"))
    fb_database = os.environ.get("FB_LOCAL_COPY_PATH", "inbox/firebird/menadzer_serwisu.fdb")
    repo_root = Path(__file__).resolve().parents[2]
    db_path = Path(fb_database)
    if not db_path.is_absolute():
        db_path = repo_root / db_path
    if not db_path.exists():
        raise FileNotFoundError(f"Brak lokalnej kopii Firebird: {db_path}")
    fb_user = os.environ.get("FB_USER", "SYSDBA")
    fb_password = os.environ.get("FB_PASSWORD", "masterkey")
    fb_charset = os.environ.get("FB_CHARSET", "WIN1250")

    return firebirdsql.connect(
        host="127.0.0.1",
        port=fb_port,
        database=str(db_path),
        user=fb_user,
        password=fb_password,
        charset=fb_charset,
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
                WHERE NIP CONTAINING ?
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

    serial_idx = headers.get("serial", headers.get("sn", 2))
    ewidencja_idx = headers.get("ewidencja", 3)
    model_idx = headers.get("model", 1)

    output: list[dict[str, str]] = []
    for row_no, row in enumerate(values[1:], start=2):
        if not any((cell or "").strip() for cell in row):
            continue
        serial = row[serial_idx].strip() if serial_idx < len(row) else ""
        ewidencja = row[ewidencja_idx].strip() if ewidencja_idx < len(row) else ""
        model = row[model_idx].strip() if model_idx < len(row) else ""
        output.append(
            {
                "row": str(row_no),
                "serial": serial,
                "ewidencja": ewidencja,
                "model": model,
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
                WHERE UPPER(REPLACE(REPLACE(SERIAL, '-', ''), ' ', '')) = ?
                   OR UPPER(REPLACE(REPLACE(SERIAL2, '-', ''), ' ', '')) = ?
                   OR UPPER(REPLACE(REPLACE(EWIDENCJA, '-', ''), ' ', '')) = ?
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
    "FirebirdClientMatch",
    "find_client_in_firebird",
    "find_device_in_firebird",
    "load_devices_from_sheet",
    "load_submitted_forms",
    "normalize_device_key",
    "normalize_nip",
]
