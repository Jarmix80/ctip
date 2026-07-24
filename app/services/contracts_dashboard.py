"""Logika dashboardu obslugi umow (formularze + Firebird + Google Sheets)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import FormRequest
from app.services.firebird_runtime import (
    FirebirdRuntimeConfig,
    firebird_writes_enabled,
    load_firebird_runtime_config,
    use_firebird_runtime_config,
)
from app.services.firebird_runtime import (
    firebird_connection as _firebird_connection,
)


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
class FirebirdClientDevice:
    """Urządzenie serwisowe przypisane do klienta w Menadżerze Serwisu."""

    id_maszyna: int | None
    id_maszyna_table: int | None
    id_klient: int | None
    id_umowacpc: int | None
    id_model: int | None
    marka: str | None
    model: str | None
    grupa: str | None
    serial: str | None
    serial2: str | None
    ewidencja: str | None
    aktywna: str | None
    synwp: int | None
    rodzaj_us: str | None
    typ: str | None
    kolorowa: str | None

    def as_dict(self) -> dict[str, Any]:
        """Zwraca urządzenie jako słownik bezpieczny do odpowiedzi API."""
        return {
            "id_maszyna": self.id_maszyna,
            "id_maszyna_table": self.id_maszyna_table,
            "id_klient": self.id_klient,
            "id_umowacpc": self.id_umowacpc,
            "id_model": self.id_model,
            "marka": self.marka,
            "model": self.model,
            "grupa": self.grupa,
            "serial": self.serial,
            "serial2": self.serial2,
            "ewidencja": self.ewidencja,
            "aktywna": self.aktywna,
            "synwp": self.synwp,
            "rodzaj_us": self.rodzaj_us,
            "typ": self.typ,
            "kolorowa": self.kolorowa,
        }


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
    grupa: str | None = None
    rodzaj: str | None = None
    kolor: str | None = None
    plik: str | None = None
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


def _normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _normalize_model_name_for_brand(brand: str | None, model_name: str | None) -> str:
    """Normalizuje znane warianty zapisu modelu dla producenta."""
    brand_value = _normalize_spaces(str(brand or ""))
    model_value = _normalize_spaces(str(model_name or ""))
    if not brand_value or not model_value:
        return model_value

    if brand_value.upper() not in {"RICOH", "NASHUATEC"}:
        return model_value

    condensed = re.sub(r"\s+", "", model_value).upper()
    imc_match = re.match(r"^IMC(\d+[A-Z0-9]*)$", condensed)
    if imc_match:
        return f"IM C{imc_match.group(1)}"

    mpc_match = re.match(r"^MPC(\d+[A-Z0-9]*)$", condensed)
    if mpc_match:
        return f"MP C{mpc_match.group(1)}"

    return model_value


def extract_stock_device_identity(
    raw_name: str | None,
    *,
    index_value: str | None = None,
    producer: str | None = None,
    model: str | None = None,
) -> dict[str, str]:
    """Wyciąga z nazwy pozycji magazynowej producenta, model, serial i numer wew."""
    raw = _normalize_spaces(str(raw_name or ""))
    serial = ""
    ewidencja = ""
    if raw:
        serial_match = re.search(
            r"(?i)\bS\s*/?\s*N\s*[:#-]?\s*([A-Z0-9][A-Z0-9._/-]{3,})",
            raw,
        )
        if serial_match:
            serial = _normalize_spaces(serial_match.group(1)).strip(" ,;")

        ewidencja_match = re.search(
            r"(?i)\bnr\.?\s*wew\.?\s*[:#-]?\s*([^,;]+)",
            raw,
        )
        if ewidencja_match:
            ewidencja = _normalize_spaces(ewidencja_match.group(1)).strip(" ,;")

    cleaned = re.split(r"(?i)\bS\s*/?\s*N\s*[:#-]?", raw, maxsplit=1)[0]
    cleaned = re.split(r"(?i)\bnr\.?\s*wew\.?\s*[:#-]?", cleaned, maxsplit=1)[0]
    cleaned = cleaned.strip(" ,;-")
    cleaned = _normalize_spaces(cleaned)

    producer_value = _normalize_spaces(str(producer or ""))
    model_value = _normalize_spaces(str(model or ""))

    if cleaned:
        if producer_value:
            prefixed = re.match(
                rf"(?i)^{re.escape(producer_value)}\s+(.*)$",
                cleaned,
            )
            if prefixed:
                model_value = _normalize_spaces(prefixed.group(1)) or model_value
            elif not model_value:
                model_value = cleaned
        else:
            tokens = cleaned.split(" ", 1)
            if len(tokens) == 2 and re.fullmatch(r"[A-Za-z][A-Za-z0-9._-]{1,30}", tokens[0]):
                producer_value = tokens[0]
                model_value = _normalize_spaces(tokens[1])
            elif not model_value:
                model_value = cleaned

    model_value = _normalize_model_name_for_brand(producer_value, model_value)

    if not ewidencja:
        ewidencja = _normalize_spaces(str(index_value or ""))

    return {
        "producer": producer_value,
        "model": model_value,
        "serial": serial,
        "ewidencja": ewidencja,
        "name": cleaned or raw,
    }


def _model_search_candidates(model_name: str | None) -> list[str]:
    """Buduje liste kandydatow modelu, czyszczac techniczne koncowki typu S/N."""
    raw = _truncate_text(model_name, 160)
    if not raw:
        return []

    primary = _normalize_spaces(raw)
    candidates: list[str] = [primary]

    stripped = re.split(r"(?i)\bS\s*/?\s*N\s*[:#-]?", primary, maxsplit=1)[0]
    stripped = re.split(r"(?i)\bnr\.?\s*wew\.?\s*[:#-]?", stripped, maxsplit=1)[0]
    stripped = re.split(r"(?i)\bnr\.?\s*ewid", stripped, maxsplit=1)[0]
    stripped = stripped.strip(" ,;-")
    stripped = _normalize_spaces(stripped)
    if stripped and stripped not in candidates:
        candidates.append(stripped)

    if "," in stripped:
        head = _normalize_spaces(stripped.split(",", maxsplit=1)[0].strip(" ,;-"))
        if head and head not in candidates:
            candidates.append(head)

    parsed = extract_stock_device_identity(stripped)
    parsed_model = _normalize_spaces(parsed.get("model") or "")
    parsed_producer = _normalize_spaces(parsed.get("producer") or "")
    if parsed_model and parsed_model not in candidates:
        candidates.append(parsed_model)
    if parsed_producer and parsed_model:
        combined = _normalize_spaces(f"{parsed_producer} {parsed_model}")
        if combined and combined not in candidates:
            candidates.append(combined)

    return [candidate[:100] for candidate in candidates if candidate]


def _build_model_match_from_row(row: tuple[Any, ...]) -> FirebirdModelMatch:
    return FirebirdModelMatch(
        found=True,
        id_model=int(row[0]) if row[0] is not None else None,
        marka=_truncate_text(str(row[1]) if row[1] is not None else "", 50),
        model=_truncate_text(str(row[2]) if row[2] is not None else "", 50),
        grupa=_truncate_text(str(row[3]) if row[3] is not None else "", 50),
        rodzaj=_truncate_text(str(row[4]) if row[4] is not None else "", 50),
        kolor=_truncate_text(str(row[5]) if row[5] is not None else "", 50),
        plik=_truncate_text(str(row[6]) if row[6] is not None else "", 250),
    )


def create_client_from_submitted_payload(
    payload: dict[str, Any],
    *,
    source_name: str | None = None,
    kto: str = "CTIP",
) -> FirebirdClientWriteResult:
    """Tworzy klienta w aktywnej bazie Firebird na podstawie formularza SUBMITTED."""
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
    candidates = _model_search_candidates(model_name)
    if not candidates:
        return FirebirdModelMatch(found=False)

    try:
        connection = _firebird_connection()
        cursor = connection.cursor()
        try:
            for candidate in candidates:
                cursor.execute(
                    """
                    SELECT FIRST 1
                        ID_MODEL,
                        MARKA,
                        MODEL,
                        GRUPA,
                        RODZAJ,
                        KOLOR,
                        PLIK
                    FROM MODEL
                    WHERE UPPER(TRIM(MODEL)) = UPPER(TRIM(?))
                       OR UPPER(TRIM(MARKA || ' ' || MODEL)) = UPPER(TRIM(?))
                    ORDER BY ID_MODEL DESC
                    """,
                    (candidate, candidate),
                )
                row = cursor.fetchone()
                if row:
                    return _build_model_match_from_row(row)

                cursor.execute(
                    """
                    SELECT FIRST 1
                        ID_MODEL,
                        MARKA,
                        MODEL,
                        GRUPA,
                        RODZAJ,
                        KOLOR,
                        PLIK
                    FROM MODEL
                    WHERE UPPER(TRIM(MODEL)) CONTAINING UPPER(TRIM(?))
                       OR UPPER(TRIM(MARKA || ' ' || MODEL)) CONTAINING UPPER(TRIM(?))
                    ORDER BY ID_MODEL DESC
                    """,
                    (candidate, candidate),
                )
                row = cursor.fetchone()
                if row:
                    return _build_model_match_from_row(row)

            return FirebirdModelMatch(found=False)
        finally:
            cursor.close()
            connection.close()
    except Exception as exc:  # noqa: BLE001
        return FirebirdModelMatch(found=False, error=str(exc))


def find_model_in_firebird_by_id(model_id: int | None) -> FirebirdModelMatch:
    """Wyszukuje model po ID_MODEL."""
    try:
        resolved_id = int(model_id)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        resolved_id = 0
    if resolved_id <= 0:
        return FirebirdModelMatch(found=False)

    try:
        connection = _firebird_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT FIRST 1
                    ID_MODEL,
                    MARKA,
                    MODEL,
                    GRUPA,
                    RODZAJ,
                    KOLOR,
                    PLIK
                FROM MODEL
                WHERE ID_MODEL = ?
                """,
                (resolved_id,),
            )
            row = cursor.fetchone()
            if not row:
                return FirebirdModelMatch(found=False)
            return _build_model_match_from_row(row)
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
    """Blokuje historyczny zapis MAGAZYN/MASZYNA bez dokumentu PZ."""
    del row, kto
    raise RuntimeError(
        "Historyczna synchronizacja arkusz → Firebird została wyłączona. "
        "Nowy egzemplarz należy przyjąć w /device/intake, co tworzy PZ, "
        "osobną kartotekę MAGAZYN i rekord MASZYNA."
    )


def find_client_in_firebird(nip: str) -> FirebirdClientMatch:
    """Wyszukuje klienta po NIP w aktywnej konfiguracji Firebird."""
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
    """Wyszukuje klienta po ID_KLIENT w aktywnej konfiguracji Firebird."""
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


def search_clients_in_firebird(
    query: str | None = None,
    *,
    nip: str | None = None,
    limit: int = 20,
) -> list[FirebirdClientMatch]:
    """Wyszukuje klientów po NIP albo fragmencie nazwy w aktywnej bazie Firebird."""
    cleaned_nip = normalize_nip(nip or query or "")
    text_query = str(query or "").strip()
    safe_limit = max(1, min(int(limit), 100))
    if not cleaned_nip and len(text_query) < 2:
        return []

    conditions: list[str] = []
    params: list[Any] = []
    if cleaned_nip:
        conditions.append(
            """
            REPLACE(
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
            """
        )
        params.append(cleaned_nip)
    if text_query and not text_query.isdigit():
        conditions.append("UPPER(COALESCE(NAZWA, '')) CONTAINING ?")
        params.append(text_query.upper())

    if not conditions:
        return []

    try:
        connection = _firebird_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"""
                SELECT FIRST {safe_limit} ID_KLIENT, NAZWA, NIP, TELEFON, E_MAIL
                FROM KLIENT
                WHERE {' OR '.join(conditions)}
                ORDER BY NAZWA ASC, ID_KLIENT ASC
                """,
                tuple(params),
            )
            output: list[FirebirdClientMatch] = []
            for row in cursor.fetchall():
                output.append(
                    FirebirdClientMatch(
                        found=True,
                        id_klient=int(row[0]) if row[0] is not None else None,
                        nazwa=str(row[1]) if row[1] is not None else None,
                        nip=str(row[2]) if row[2] is not None else None,
                        telefon=str(row[3]) if row[3] is not None else None,
                        email=str(row[4]) if row[4] is not None else None,
                    )
                )
            return output
        finally:
            cursor.close()
            connection.close()
    except Exception as exc:  # noqa: BLE001
        return [FirebirdClientMatch(found=False, error=str(exc))]


def _build_firebird_client_device(row: tuple[Any, ...]) -> FirebirdClientDevice:
    return FirebirdClientDevice(
        id_maszyna=int(row[0]) if row[0] is not None else None,
        id_maszyna_table=int(row[1]) if row[1] is not None else None,
        id_klient=int(row[2]) if row[2] is not None else None,
        id_umowacpc=int(row[3]) if row[3] is not None else None,
        id_model=int(row[4]) if row[4] is not None else None,
        marka=str(row[5]) if row[5] is not None else None,
        model=str(row[6]) if row[6] is not None else None,
        grupa=str(row[7]) if row[7] is not None else None,
        serial=str(row[8]) if row[8] is not None else None,
        serial2=str(row[9]) if row[9] is not None else None,
        ewidencja=str(row[10]) if row[10] is not None else None,
        aktywna=str(row[11]) if row[11] is not None else None,
        synwp=int(row[12]) if row[12] is not None else None,
        rodzaj_us=str(row[13]) if row[13] is not None else None,
        typ=str(row[14]) if row[14] is not None else None,
        kolorowa=str(row[15]) if row[15] is not None else None,
    )


def load_client_devices_from_firebird(
    client_id: int,
    *,
    include_inactive: bool = False,
    limit: int = 500,
) -> list[FirebirdClientDevice]:
    """Pobiera urządzenia serwisowe klienta z tabeli MASZYNA."""
    safe_limit = max(1, min(int(limit), 2000))
    try:
        connection = _firebird_connection()
        cursor = connection.cursor()
        try:
            active_filter = ""
            if not include_inactive:
                active_filter = "AND UPPER(COALESCE(AKTYWNA, 'TAK')) <> 'NIE'"
            cursor.execute(
                f"""
                SELECT FIRST {safe_limit}
                    ID_MASZYNA,
                    ID_MASZYNA_TABLE,
                    ID_KLIENT,
                    ID_UMOWACPC,
                    ID_MODEL,
                    MARKA,
                    MODEL,
                    GRUPA,
                    SERIAL,
                    SERIAL2,
                    EWIDENCJA,
                    AKTYWNA,
                    SYNWP,
                    RODZAJ_US,
                    TYP,
                    KOLOROWA
                FROM MASZYNA
                WHERE ID_KLIENT = ?
                {active_filter}
                ORDER BY ID_MASZYNA DESC
                """,
                (int(client_id),),
            )
            return [_build_firebird_client_device(row) for row in cursor.fetchall()]
        finally:
            cursor.close()
            connection.close()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Nie udało się pobrać urządzeń klienta z Firebird: {exc}") from exc


def _sheet_headers_map(headers: list[str]) -> dict[str, int]:
    mapped: dict[str, int] = {}
    for idx, col in enumerate(headers):
        normalized = col.strip().lower()
        mapped[normalized] = idx
    return mapped


def _decimal_or_zero(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _format_decimal_text(value: Any, *, precision: str = "0.01") -> str:
    decimal_value = _decimal_or_zero(value).quantize(Decimal(precision))
    return format(decimal_value, "f")


def _format_quantity_text(value: Any) -> str:
    decimal_value = _decimal_or_zero(value)
    normalized = decimal_value.normalize()
    return format(normalized, "f").rstrip("0").rstrip(".") or "0"


def _optional_flag_bool(value: Any) -> bool | None:
    """Normalizuje opcjonalną flagę Firebird do wartości logicznej."""
    normalized = str(value or "").strip().upper()
    if normalized in {"1", "TAK", "TRUE", "Y"}:
        return True
    if normalized in {"0", "NIE", "FALSE", "N"}:
        return False
    return None


def _model_color_key(producer: Any, model: Any) -> tuple[str, str]:
    """Buduje znormalizowany klucz producenta i modelu dla flagi kolorowości."""
    producer_value = normalize_device_key(str(producer or ""))
    model_value = normalize_device_key(
        _normalize_model_name_for_brand(
            str(producer or ""),
            str(model or ""),
        )
    )
    return producer_value, model_value


def _build_model_color_lookup(
    rows: list[tuple[Any, ...]],
) -> tuple[dict[tuple[str, str], bool], dict[str, bool]]:
    """Buduje jednoznaczne mapy kolorowości modeli z tabeli MODEL."""
    by_identity: dict[tuple[str, str], bool] = {}
    values_by_model: dict[str, set[bool]] = {}
    for producer, model, raw_flag in rows:
        color_flag = _optional_flag_bool(raw_flag)
        producer_key, model_key = _model_color_key(producer, model)
        if color_flag is None or not model_key:
            continue
        by_identity[(producer_key, model_key)] = color_flag
        values_by_model.setdefault(model_key, set()).add(color_flag)
    by_model = {
        model_key: next(iter(flags))
        for model_key, flags in values_by_model.items()
        if len(flags) == 1
    }
    return by_identity, by_model


def _resolve_model_color(
    *,
    producer: Any,
    model: Any,
    direct_flag: Any,
    by_identity: dict[tuple[str, str], bool],
    by_model: dict[str, bool],
) -> bool | None:
    """Rozwiązuje kolorowość po ID, katalogu nazw i jednoznacznym oznaczeniu modelu."""
    direct_value = _optional_flag_bool(direct_flag)
    if direct_value is not None:
        return direct_value
    producer_key, model_key = _model_color_key(producer, model)
    if not model_key:
        return None
    identity_key = (producer_key, model_key)
    if identity_key in by_identity:
        return by_identity[identity_key]
    if model_key in by_model:
        return by_model[model_key]
    compact_name = normalize_device_key(f"{producer or ''} {model or ''}")
    if (
        re.search(r"(?:IMC|MPC)\d", compact_name)
        or "DESIGNJET" in compact_name
        or any(marker in compact_name for marker in ("COLOR", "COLOUR", "KOLOR"))
        or (
            any(brand in compact_name for brand in ("KONICA", "MINOLTA", "CANON"))
            and re.search(r"C\d{3,5}", compact_name)
        )
    ):
        return True
    return False


def _extract_vat_rate_text(value: Any) -> str:
    raw = str(value or "").strip().replace(",", ".")
    match = re.search(r"(\d+(?:\.\d+)?)", raw)
    if not match:
        return "23"
    return match.group(1).rstrip("0").rstrip(".") or "23"


def load_available_devices_from_firebird_warehouse(
    *,
    limit: int = 500,
    source_row: int | None = None,
) -> list[dict[str, Any]]:
    """Pobiera dostępne egzemplarze i kolorowość modelu z magazynu Firebird."""
    safe_limit = max(1, min(int(limit), 2000))
    source_row_filter = ""
    query_params: list[int] = [settings.fb_warehouse_id]
    if source_row is not None:
        source_row_filter = " AND mg.ID_MAGAZYN_TABLE = ?"
        query_params.append(int(source_row))
    try:
        connection = _firebird_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"""
                SELECT FIRST {safe_limit}
                    mg.ID_MAGAZYN_TABLE,
                    mg.ID_MODEL,
                    mg.INDEKS,
                    mg.NAZWA,
                    mg.MARKA,
                    mg.MODEL,
                    mg.ILOSC,
                    mg.IL_REZ,
                    mg.CENA_NETTO,
                    mg.CENA_BRUTTO,
                    mg.VAT_STAWKA,
                    mg.SERIAL,
                    mg.CENA_Z1,
                    model.KOLOR
                FROM MAGAZYN mg
                LEFT JOIN MODEL model ON model.ID_MODEL = mg.ID_MODEL
                WHERE COALESCE(mg.ID_MAGAZYN, 0) = ?
                  AND COALESCE(mg.ILOSC, 0) > 0
                  {source_row_filter}
                ORDER BY mg.ID_MAGAZYN_TABLE DESC
                """,
                tuple(query_params),
            )

            warehouse_rows = list(cursor.fetchall())
            cursor.execute(
                """
                SELECT MARKA, MODEL, KOLOR
                FROM MODEL
                WHERE KOLOR IS NOT NULL
                  AND TRIM(KOLOR) <> ''
                """
            )
            model_color_by_identity, model_color_by_model = _build_model_color_lookup(
                list(cursor.fetchall())
            )
            cursor.execute(
                """
                SELECT ID_MASZYNA, SERIAL, EWIDENCJA
                FROM MASZYNA
                WHERE COALESCE(TRIM(SERIAL), '') <> ''
                   OR COALESCE(TRIM(EWIDENCJA), '') <> ''
                """
            )
            machine_by_serial: dict[str, list[int]] = {}
            machine_by_index: dict[str, list[int]] = {}
            for machine_id, machine_serial, machine_index in cursor.fetchall():
                parsed_machine_id = int(machine_id) if machine_id is not None else 0
                serial_key = normalize_device_key(str(machine_serial or ""))
                index_key = normalize_device_key(str(machine_index or ""))
                if serial_key:
                    machine_by_serial.setdefault(serial_key, []).append(parsed_machine_id)
                if index_key:
                    machine_by_index.setdefault(index_key, []).append(parsed_machine_id)

            output: list[dict[str, Any]] = []
            for row in warehouse_rows:
                total_qty = _decimal_or_zero(row[6])
                reserved_qty = _decimal_or_zero(row[7])
                available_qty = total_qty - reserved_qty
                if available_qty <= 0:
                    continue

                if reserved_qty > 0:
                    reservation_status = (
                        f"czesciowa rezerwacja ({_format_quantity_text(reserved_qty)} z "
                        f"{_format_quantity_text(total_qty)})"
                    )
                    status = "Dostepne czesciowo"
                else:
                    reservation_status = "brak rezerwacji"
                    status = "Dostepne"

                index_value = _truncate_text(str(row[2] or ""), 100) or ""
                name_value = _truncate_text(str(row[3] or ""), 250) or ""
                parsed_identity = extract_stock_device_identity(
                    name_value,
                    index_value=index_value,
                    producer=str(row[4] or ""),
                    model=str(row[5] or ""),
                )
                producer_value = (
                    _truncate_text(
                        str(row[4] or parsed_identity["producer"] or ""),
                        50,
                    )
                    or ""
                )
                model_value = (
                    _truncate_text(
                        str(row[5] or parsed_identity["model"] or ""),
                        50,
                    )
                    or name_value
                )
                serial_value = _truncate_text(str(parsed_identity["serial"] or ""), 100) or ""
                ewidencja_value = (
                    _truncate_text(
                        str(parsed_identity["ewidencja"] or index_value),
                        100,
                    )
                    or index_value
                )
                raw_serial_flag = _truncate_text(str(row[11] or ""), 10) or ""
                serial_required = "TAK" if serial_value else raw_serial_flag
                model_id_value = str(int(row[1])) if row[1] is not None else ""
                machine_candidates = machine_by_serial.get(
                    normalize_device_key(serial_value),
                    [],
                )
                if not machine_candidates:
                    machine_candidates = machine_by_index.get(
                        normalize_device_key(ewidencja_value),
                        [],
                    )
                unique_machine_ids = {
                    machine_id for machine_id in machine_candidates if machine_id > 0
                }

                output.append(
                    {
                        "row": str(int(row[0])) if row[0] is not None else "",
                        "ms_id_magazyn_table": str(int(row[0])) if row[0] is not None else "",
                        "ms_id_model": model_id_value,
                        "producer": producer_value,
                        "model": model_value,
                        "serial": serial_value,
                        "ewidencja": ewidencja_value,
                        "index": index_value,
                        "name": name_value,
                        "status": status,
                        "price": _format_decimal_text(row[9]),
                        "price_net": _format_decimal_text(row[8]),
                        "price_gross": _format_decimal_text(row[9]),
                        "purchase_price_net": _format_decimal_text(row[12]),
                        "vat_rate": _extract_vat_rate_text(row[10]),
                        "reservation": "",
                        "reservation_status": reservation_status,
                        "description": name_value,
                        "available_quantity": _format_quantity_text(available_qty),
                        "reserved_quantity": _format_quantity_text(reserved_qty),
                        "warehouse_quantity": _format_quantity_text(total_qty),
                        "serial_required": serial_required,
                        "is_color": _resolve_model_color(
                            producer=producer_value,
                            model=model_value,
                            direct_flag=row[13],
                            by_identity=model_color_by_identity,
                            by_model=model_color_by_model,
                        ),
                        "source_type": "firebird_magazyn_28",
                        "machine_present": len(unique_machine_ids) == 1,
                        "machine_id": (
                            next(iter(unique_machine_ids)) if len(unique_machine_ids) == 1 else None
                        ),
                        "machine_ambiguous": len(unique_machine_ids) > 1,
                    }
                )
            return output
        finally:
            cursor.close()
            connection.close()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Nie udalo sie pobrac listy urzadzen z Firebird: {exc}") from exc


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
    ms_id_model_idx = headers.get("ms_id_model")
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
                "ms_id_model": _cell(row, ms_id_model_idx),
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
    "FirebirdClientDevice",
    "FirebirdClientMatch",
    "FirebirdDeviceSyncResult",
    "FirebirdModelMatch",
    "FirebirdRuntimeConfig",
    "FirebirdWarehouseMatch",
    "create_client_from_submitted_payload",
    "find_model_in_firebird",
    "find_model_in_firebird_by_id",
    "find_warehouse_item_in_firebird",
    "find_client_in_firebird",
    "find_client_in_firebird_by_id",
    "find_device_in_firebird",
    "firebird_writes_enabled",
    "extract_stock_device_identity",
    "load_contract_forms",
    "load_available_devices_from_firebird_warehouse",
    "load_client_devices_from_firebird",
    "load_device_from_sheet_row",
    "load_devices_from_sheet",
    "load_firebird_runtime_config",
    "load_submitted_forms",
    "normalize_device_key",
    "normalize_nip",
    "search_clients_in_firebird",
    "synchronize_device_from_sheet_row",
    "use_firebird_runtime_config",
]
