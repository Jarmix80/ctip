"""Obsługa wykupu urządzeń po zakończeniu wynajmu BNP."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from app.services.device_intake import (
    DEFAULT_PAYMENT,
    DEFAULT_PLACE,
    DEFAULT_UNIT,
    DEFAULT_VAT_RATE,
    ITEM_KIND_TOWAR_INNY,
    _firebird_connection,
    _money,
    _next_pz_document_number,
    _parse_vat_rate,
    _text,
)

BNP_BUYOUT_WAREHOUSE_ID = 27
BNP_SUPPLIER_NIP = "1132061128"
BNP_INDEX_SUFFIX = "/BNP"
_KP_IDENTIFIER_PATTERN = re.compile(
    r"^(?P<prefix>W?KP)/(?P<number>\d+)(?P<suffix>/.*)?$",
    re.IGNORECASE,
)


@dataclass(slots=True)
class BnpCatalogResult:
    created: bool
    warehouse_item: dict[str, Any]


@dataclass(slots=True)
class BnpBuyoutResult:
    already_completed: bool
    pz_id: int
    pz_number: str
    zakpozycja_id: int
    warehouse_item_id: int
    warehouse_index: str
    warehouse_quantity: Decimal
    machine_id: int
    machine_table_id: int
    previous_ewidencja: str
    target_ewidencja: str
    supplier_id: int
    external_document: str
    purchase_price_netto: Decimal


def _normalize_serial_key(value: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", _text(value, 100).upper())


def _normalize_nip(value: str | None) -> str:
    return re.sub(r"[^0-9]", "", _text(value, 30))


def _parse_kp_identifier(value: str | None) -> dict[str, str] | None:
    normalized = _text(value, 100)
    match = _KP_IDENTIFIER_PATTERN.fullmatch(normalized)
    if match is None:
        return None
    return {
        "prefix": match.group("prefix").upper(),
        "number": match.group("number"),
        "suffix": match.group("suffix") or "",
    }


def _build_buyout_identifiers(source_ewidencja: str) -> tuple[str, str]:
    parsed = _parse_kp_identifier(source_ewidencja)
    if parsed is None or parsed["prefix"] != "KP":
        raise ValueError("MASZYNA.EWIDENCJA musi mieć format KP/<numer>/...")
    target_ewidencja = f"WKP/{parsed['number']}{parsed['suffix']}"
    warehouse_index = f"WKP/{parsed['number']}{BNP_INDEX_SUFFIX}"
    return target_ewidencja, warehouse_index


def _validate_target_identifier(
    value: str,
    *,
    expected_number: str,
    field_name: str,
) -> str:
    normalized = _text(value, 100)
    parsed = _parse_kp_identifier(normalized)
    if parsed is None or parsed["prefix"] != "WKP":
        raise ValueError(f"{field_name} musi mieć format WKP/<numer>/...")
    if parsed["number"] != expected_number:
        raise ValueError(
            f"{field_name} musi zachować numer KP/{expected_number}; zmiana numeru jest zablokowana."
        )
    return f"WKP/{parsed['number']}{parsed['suffix']}"


def _map_machine_row(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "id_maszyna_table": int(row[0]),
        "id_maszyna": int(row[1]) if row[1] is not None else None,
        "id_klient": int(row[2]) if row[2] is not None else None,
        "client_name": _text(row[3], 500),
        "client_nip": _text(row[4], 30),
        "id_model": int(row[5]) if row[5] is not None else None,
        "marka": _text(row[6], 50),
        "model": _text(row[7], 50),
        "serial": _text(row[8], 100),
        "serial2": _text(row[9], 100),
        "ewidencja": _text(row[10], 100),
        "aktywna": _text(row[11], 20),
        "stoi": _text(row[12], 250),
        "adres": _text(row[13], 250),
        "miejscowosc": _text(row[14], 150),
    }


def _find_machines_by_serial(cursor, serial_key: str) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT
            m.ID_MASZYNA_TABLE,
            m.ID_MASZYNA,
            m.ID_KLIENT,
            COALESCE(k.NAZWA, ''),
            COALESCE(k.NIP, ''),
            m.ID_MODEL,
            COALESCE(m.MARKA, ''),
            COALESCE(m.MODEL, ''),
            COALESCE(m.SERIAL, ''),
            COALESCE(m.SERIAL2, ''),
            COALESCE(m.EWIDENCJA, ''),
            COALESCE(m.AKTYWNA, ''),
            COALESCE(m.STOI, ''),
            COALESCE(m.ADRES, ''),
            COALESCE(m.MIEJSCOWOSC, '')
        FROM MASZYNA m
        LEFT JOIN KLIENT k ON k.ID_KLIENT = m.ID_KLIENT
        WHERE UPPER(REPLACE(REPLACE(REPLACE(COALESCE(m.SERIAL, ''), '/', ''), '-', ''), ' ', '')) = ?
           OR UPPER(REPLACE(REPLACE(REPLACE(COALESCE(m.SERIAL2, ''), '/', ''), '-', ''), ' ', '')) = ?
        ORDER BY m.ID_MASZYNA_TABLE DESC
        """,
        (serial_key, serial_key),
    )
    return [_map_machine_row(row) for row in cursor.fetchall()]


def _find_machine_for_write(
    cursor,
    *,
    machine_table_id: int,
    serial_key: str,
) -> dict[str, Any]:
    cursor.execute(
        """
        SELECT
            m.ID_MASZYNA_TABLE,
            m.ID_MASZYNA,
            m.ID_KLIENT,
            COALESCE(k.NAZWA, ''),
            COALESCE(k.NIP, ''),
            m.ID_MODEL,
            COALESCE(m.MARKA, ''),
            COALESCE(m.MODEL, ''),
            COALESCE(m.SERIAL, ''),
            COALESCE(m.SERIAL2, ''),
            COALESCE(m.EWIDENCJA, ''),
            COALESCE(m.AKTYWNA, ''),
            COALESCE(m.STOI, ''),
            COALESCE(m.ADRES, ''),
            COALESCE(m.MIEJSCOWOSC, '')
        FROM MASZYNA m
        LEFT JOIN KLIENT k ON k.ID_KLIENT = m.ID_KLIENT
        WHERE m.ID_MASZYNA_TABLE = ?
        """,
        (int(machine_table_id),),
    )
    row = cursor.fetchone()
    if row is None:
        raise ValueError(f"Nie znaleziono MASZYNA.ID_MASZYNA_TABLE={machine_table_id}.")
    machine = _map_machine_row(row)
    machine_serials = {
        _normalize_serial_key(machine.get("serial")),
        _normalize_serial_key(machine.get("serial2")),
    }
    if serial_key not in machine_serials:
        raise ValueError("Numer seryjny nie odpowiada wskazanemu rekordowi MASZYNA.")
    return machine


def _map_warehouse_row(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "id_magazyn_table": int(row[0]),
        "id_magazyn": int(row[1]) if row[1] is not None else None,
        "warehouse_name": _text(row[2], 250),
        "index": _text(row[3], 100),
        "name": _text(row[4], 250),
        "quantity": Decimal(str(row[5] or 0)),
        "purchase_price": Decimal(str(row[6] or 0)),
        "net_price": Decimal(str(row[7] or 0)),
        "id_model": int(row[8]) if row[8] is not None else None,
        "marka": _text(row[9], 50),
        "model": _text(row[10], 50),
        "serial_required": _text(row[11], 20),
    }


def _find_warehouse_rows(cursor, kp_number: str) -> list[dict[str, Any]]:
    base_index = f"WKP/{kp_number}".upper()
    cursor.execute(
        """
        SELECT
            m.ID_MAGAZYN_TABLE,
            m.ID_MAGAZYN,
            COALESCE(mg.NAZWA, ''),
            COALESCE(m.INDEKS, ''),
            COALESCE(m.NAZWA, ''),
            COALESCE(m.ILOSC, 0),
            COALESCE(m.CENA_Z1, 0),
            COALESCE(m.CENA_NETTO, 0),
            m.ID_MODEL,
            COALESCE(m.MARKA, ''),
            COALESCE(m.MODEL, ''),
            COALESCE(m.SERIAL, '')
        FROM MAGAZYN m
        LEFT JOIN MAGAZYNY mg ON mg.ID_MAGAZYN = m.ID_MAGAZYN
        WHERE UPPER(TRIM(COALESCE(m.INDEKS, ''))) = ?
           OR UPPER(TRIM(COALESCE(m.INDEKS, ''))) STARTING WITH ?
        ORDER BY m.ID_MAGAZYN, m.ID_MAGAZYN_TABLE
        """,
        (base_index, f"{base_index}/"),
    )
    return [_map_warehouse_row(row) for row in cursor.fetchall()]


def _find_bnp_supplier(cursor) -> dict[str, Any] | None:
    cursor.execute(
        """
        SELECT
            ID_KLIENT,
            COALESCE(NAZWA, ''),
            COALESCE(ADRES, ''),
            COALESCE(KOD, ''),
            COALESCE(POCZTA, ''),
            COALESCE(NIP, '')
        FROM KLIENT
        WHERE REPLACE(REPLACE(REPLACE(COALESCE(NIP, ''), '-', ''), ' ', ''), '.', '') = ?
        ORDER BY ID_KLIENT
        """,
        (BNP_SUPPLIER_NIP,),
    )
    rows = cursor.fetchall()
    if len(rows) != 1:
        return None
    row = rows[0]
    return {
        "id_klient": int(row[0]),
        "name": _text(row[1], 500),
        "address": _text(row[2], 250),
        "postal_code": _text(row[3], 6),
        "city": _text(row[4], 150),
        "nip": _normalize_nip(row[5]),
    }


def _build_lookup_payload(cursor, serial_value: str) -> dict[str, Any]:
    serial_key = _normalize_serial_key(serial_value)
    if not serial_key:
        raise ValueError("Numer seryjny jest wymagany.")

    machines = _find_machines_by_serial(cursor, serial_key)
    blockers: list[str] = []
    warnings: list[str] = []
    if not machines:
        return {
            "serial": _text(serial_value, 100),
            "machines": [],
            "machine": None,
            "supplier": _find_bnp_supplier(cursor),
            "warehouse_rows": [],
            "target_item": None,
            "suggested_ewidencja": "",
            "suggested_index": "",
            "can_create_catalog": False,
            "can_complete": False,
            "blockers": ["Nie znaleziono urządzenia po podanym numerze seryjnym."],
            "warnings": [],
        }
    if len(machines) > 1:
        return {
            "serial": _text(serial_value, 100),
            "machines": machines,
            "machine": None,
            "supplier": _find_bnp_supplier(cursor),
            "warehouse_rows": [],
            "target_item": None,
            "suggested_ewidencja": "",
            "suggested_index": "",
            "can_create_catalog": False,
            "can_complete": False,
            "blockers": [
                "Numer seryjny pasuje do wielu rekordów MASZYNA. Operacja wymaga jednoznacznego rekordu."
            ],
            "warnings": [],
        }

    machine = machines[0]
    parsed = _parse_kp_identifier(machine["ewidencja"])
    suggested_ewidencja = ""
    suggested_index = ""
    warehouse_rows: list[dict[str, Any]] = []
    target_item = None

    if parsed is None:
        blockers.append("MASZYNA.EWIDENCJA nie ma formatu KP/<numer>/...")
    else:
        if parsed["prefix"] == "WKP":
            blockers.append("Urządzenie ma już oznaczenie WKP i wygląda na wcześniej wykupione.")
        else:
            suggested_ewidencja, suggested_index = _build_buyout_identifiers(machine["ewidencja"])
        warehouse_rows = _find_warehouse_rows(cursor, parsed["number"])
        target_rows = [
            row for row in warehouse_rows if row["id_magazyn"] == BNP_BUYOUT_WAREHOUSE_ID
        ]
        if len(target_rows) > 1:
            blockers.append(
                "Na magazynie 27 istnieje więcej niż jedna kartoteka dla tego numeru KP."
            )
        elif target_rows:
            target_item = target_rows[0]
            suggested_index = target_item["index"]
            if target_item["quantity"] != Decimal("0"):
                blockers.append(
                    "Kartoteka na magazynie 27 ma stan różny od 0. Ponowny wykup został zablokowany."
                )
        if any(row["id_magazyn"] != BNP_BUYOUT_WAREHOUSE_ID for row in warehouse_rows):
            warnings.append("Znaleziono także kartoteki tego urządzenia na innych magazynach.")

    supplier = _find_bnp_supplier(cursor)
    if supplier is None:
        blockers.append(f"Nie znaleziono jednoznacznej kartoteki BNP dla NIP {BNP_SUPPLIER_NIP}.")

    can_create_catalog = (
        parsed is not None
        and parsed["prefix"] == "KP"
        and target_item is None
        and not any("więcej niż jedna kartoteka" in item for item in blockers)
        and supplier is not None
    )
    can_complete = target_item is not None and not blockers
    return {
        "serial": _text(serial_value, 100),
        "machines": machines,
        "machine": machine,
        "supplier": supplier,
        "warehouse_rows": warehouse_rows,
        "target_item": target_item,
        "suggested_ewidencja": suggested_ewidencja,
        "suggested_index": suggested_index,
        "can_create_catalog": can_create_catalog,
        "can_complete": can_complete,
        "blockers": blockers,
        "warnings": warnings,
    }


def lookup_bnp_buyout(*, serial: str) -> dict[str, Any]:
    """Zwraca podgląd urządzenia i kartotek dla procesu wykupu BNP."""
    connection = _firebird_connection()
    cursor = connection.cursor()
    try:
        return _build_lookup_payload(cursor, serial)
    finally:
        cursor.close()
        connection.close()


def create_bnp_catalog_item(
    *,
    serial: str,
    machine_table_id: int,
    expected_ewidencja: str,
    warehouse_index: str,
    item_name: str,
    kto: str,
) -> BnpCatalogResult:
    """Tworzy brakującą kartotekę wykupu BNP na magazynie 27 ze stanem 0."""
    serial_key = _normalize_serial_key(serial)
    normalized_name = _text(item_name, 250)
    if not normalized_name:
        raise ValueError("Nazwa pozycji z dokumentu BNP jest wymagana.")

    connection = _firebird_connection()
    cursor = connection.cursor()
    try:
        machine = _find_machine_for_write(
            cursor,
            machine_table_id=machine_table_id,
            serial_key=serial_key,
        )
        if machine["ewidencja"].upper() != _text(expected_ewidencja, 100).upper():
            raise ValueError(
                "MASZYNA.EWIDENCJA zmieniła się od czasu wyszukania. Odśwież dane urządzenia."
            )
        parsed = _parse_kp_identifier(machine["ewidencja"])
        if parsed is None or parsed["prefix"] != "KP":
            raise ValueError("Kartotekę można utworzyć tylko dla oznaczenia KP/<numer>/...")
        normalized_index = _validate_target_identifier(
            warehouse_index,
            expected_number=parsed["number"],
            field_name="MAGAZYN.INDEKS",
        )

        target_rows = [
            row
            for row in _find_warehouse_rows(cursor, parsed["number"])
            if row["id_magazyn"] == BNP_BUYOUT_WAREHOUSE_ID
        ]
        if len(target_rows) > 1:
            raise ValueError(
                "Na magazynie 27 istnieje więcej niż jedna kartoteka dla tego numeru KP."
            )
        if target_rows:
            connection.rollback()
            return BnpCatalogResult(created=False, warehouse_item=target_rows[0])

        cursor.execute(
            """
            INSERT INTO MAGAZYN (
                ID_ODDZIAL,
                ID_FIRMA,
                ID_MAGAZYN,
                RODZAJ,
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
                SYNWP,
                V_2010A,
                UWAGI
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING ID_MAGAZYN_TABLE
            """,
            (
                1,
                1,
                BNP_BUYOUT_WAREHOUSE_ID,
                ITEM_KIND_TOWAR_INNY,
                normalized_index,
                normalized_name,
                DEFAULT_UNIT,
                Decimal("0"),
                date.today(),
                machine["marka"],
                machine["model"],
                machine["id_model"],
                "NIE",
                1,
                DEFAULT_VAT_RATE,
                1,
                "TAK",
                _text(f"CTIP WYKUP BNP {kto}", 1000),
            ),
        )
        row = cursor.fetchone()
        if row is None or row[0] is None:
            raise RuntimeError("Nie udało się utworzyć kartoteki MAGAZYN.")
        created_id = int(row[0])
        created_rows = [
            item
            for item in _find_warehouse_rows(cursor, parsed["number"])
            if item["id_magazyn_table"] == created_id
        ]
        if len(created_rows) != 1:
            raise RuntimeError("Nie udało się odczytać utworzonej kartoteki MAGAZYN.")
        connection.commit()
        return BnpCatalogResult(created=True, warehouse_item=created_rows[0])
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


def _find_existing_document(
    cursor,
    *,
    supplier_id: int,
    external_document: str,
) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT
            z.ID_ZAKUPY_TABLE,
            COALESCE(z.NUMER, ''),
            zp.ID_ZAKPOZYCJA_TABLE,
            zp.ID_MAGAZYN,
            COALESCE(zp.INDEKS, '')
        FROM ZAKUPY z
        JOIN ZAKPOZYCJA zp ON zp.ID_ZAKUPY = z.ID_ZAKUPY_TABLE
        WHERE z.RODZAJ_DOK = 'PZ'
          AND z.ID_KLIENT = ?
          AND UPPER(TRIM(COALESCE(z.DOK_ZEW, ''))) = ?
        ORDER BY z.ID_ZAKUPY_TABLE DESC, zp.ID_ZAKPOZYCJA_TABLE DESC
        """,
        (supplier_id, external_document.upper()),
    )
    return [
        {
            "pz_id": int(row[0]),
            "pz_number": _text(row[1], 30),
            "zakpozycja_id": int(row[2]),
            "warehouse_item_id": int(row[3]),
            "warehouse_index": _text(row[4], 100),
        }
        for row in cursor.fetchall()
    ]


def _find_existing_buyout_by_number(
    cursor,
    *,
    supplier_id: int,
    kp_number: str,
) -> list[dict[str, Any]]:
    base_index = f"WKP/{kp_number}".upper()
    cursor.execute(
        """
        SELECT FIRST 10
            z.ID_ZAKUPY_TABLE,
            COALESCE(z.NUMER, ''),
            COALESCE(z.DOK_ZEW, ''),
            zp.ID_ZAKPOZYCJA_TABLE,
            zp.ID_MAGAZYN,
            COALESCE(zp.INDEKS, '')
        FROM ZAKUPY z
        JOIN ZAKPOZYCJA zp ON zp.ID_ZAKUPY = z.ID_ZAKUPY_TABLE
        WHERE z.RODZAJ_DOK = 'PZ'
          AND z.ID_KLIENT = ?
          AND (
              UPPER(TRIM(COALESCE(zp.INDEKS, ''))) = ?
              OR UPPER(TRIM(COALESCE(zp.INDEKS, ''))) STARTING WITH ?
          )
        ORDER BY z.ID_ZAKUPY_TABLE DESC, zp.ID_ZAKPOZYCJA_TABLE DESC
        """,
        (supplier_id, base_index, f"{base_index}/"),
    )
    return [
        {
            "pz_id": int(row[0]),
            "pz_number": _text(row[1], 30),
            "external_document": _text(row[2], 30),
            "zakpozycja_id": int(row[3]),
            "warehouse_item_id": int(row[4]),
            "warehouse_index": _text(row[5], 100),
        }
        for row in cursor.fetchall()
    ]


def _build_existing_result(
    *,
    existing: dict[str, Any],
    warehouse_item: dict[str, Any],
    machine: dict[str, Any],
    previous_ewidencja: str,
    target_ewidencja: str,
    supplier_id: int,
    external_document: str,
    purchase_price_netto: Decimal,
) -> BnpBuyoutResult:
    return BnpBuyoutResult(
        already_completed=True,
        pz_id=int(existing["pz_id"]),
        pz_number=str(existing["pz_number"]),
        zakpozycja_id=int(existing["zakpozycja_id"]),
        warehouse_item_id=int(warehouse_item["id_magazyn_table"]),
        warehouse_index=str(warehouse_item["index"]),
        warehouse_quantity=Decimal(str(warehouse_item["quantity"])),
        machine_id=int(machine["id_maszyna"]),
        machine_table_id=int(machine["id_maszyna_table"]),
        previous_ewidencja=previous_ewidencja,
        target_ewidencja=target_ewidencja,
        supplier_id=supplier_id,
        external_document=external_document,
        purchase_price_netto=purchase_price_netto,
    )


def complete_bnp_buyout(
    *,
    serial: str,
    machine_table_id: int,
    warehouse_item_id: int,
    expected_ewidencja: str,
    target_ewidencja: str,
    warehouse_index: str,
    item_name: str,
    external_document: str,
    document_date: date,
    purchase_price_netto: Decimal,
    issued_by: str,
    kto: str,
) -> BnpBuyoutResult:
    """Tworzy PZ wykupu BNP i zmienia oznaczenie urządzenia na WKP."""
    serial_key = _normalize_serial_key(serial)
    normalized_name = _text(item_name, 250)
    normalized_document = _text(external_document, 30)
    price_net = _money(purchase_price_netto)
    if not normalized_name:
        raise ValueError("Nazwa pozycji z dokumentu BNP jest wymagana.")
    if not normalized_document:
        raise ValueError("Numer dokumentu BNP jest wymagany.")
    if price_net <= Decimal("0"):
        raise ValueError("Cena netto wykupu musi być większa od 0.")

    connection = _firebird_connection()
    cursor = connection.cursor()
    try:
        machine = _find_machine_for_write(
            cursor,
            machine_table_id=machine_table_id,
            serial_key=serial_key,
        )
        source_parsed = _parse_kp_identifier(expected_ewidencja)
        if source_parsed is None or source_parsed["prefix"] != "KP":
            raise ValueError("Oczekiwane oznaczenie źródłowe musi mieć format KP/<numer>/...")
        normalized_target = _validate_target_identifier(
            target_ewidencja,
            expected_number=source_parsed["number"],
            field_name="MASZYNA.EWIDENCJA",
        )
        normalized_index = _validate_target_identifier(
            warehouse_index,
            expected_number=source_parsed["number"],
            field_name="MAGAZYN.INDEKS",
        )

        supplier = _find_bnp_supplier(cursor)
        if supplier is None:
            raise ValueError(
                f"Nie znaleziono jednoznacznej kartoteki BNP dla NIP {BNP_SUPPLIER_NIP}."
            )
        supplier_id = int(supplier["id_klient"])

        target_rows = [
            row
            for row in _find_warehouse_rows(cursor, source_parsed["number"])
            if row["id_magazyn"] == BNP_BUYOUT_WAREHOUSE_ID
        ]
        if len(target_rows) != 1:
            raise ValueError("Wykup wymaga dokładnie jednej kartoteki urządzenia na magazynie 27.")
        warehouse_item = target_rows[0]
        if int(warehouse_item["id_magazyn_table"]) != int(warehouse_item_id):
            raise ValueError("Kartoteka magazynowa zmieniła się od czasu wyszukania.")

        existing_document_rows = _find_existing_document(
            cursor,
            supplier_id=supplier_id,
            external_document=normalized_document,
        )
        current_ewidencja = _text(machine["ewidencja"], 100)
        if current_ewidencja.upper() == normalized_target.upper():
            matching_existing = next(
                (
                    row
                    for row in existing_document_rows
                    if int(row["warehouse_item_id"]) == int(warehouse_item_id)
                ),
                None,
            )
            if matching_existing is not None:
                connection.rollback()
                return _build_existing_result(
                    existing=matching_existing,
                    warehouse_item=warehouse_item,
                    machine=machine,
                    previous_ewidencja=_text(expected_ewidencja, 100),
                    target_ewidencja=normalized_target,
                    supplier_id=supplier_id,
                    external_document=normalized_document,
                    purchase_price_netto=price_net,
                )
            raise ValueError(
                "Urządzenie ma już oznaczenie docelowe WKP, ale nie znaleziono zgodnego PZ."
            )
        if current_ewidencja.upper() != _text(expected_ewidencja, 100).upper():
            raise ValueError(
                "MASZYNA.EWIDENCJA zmieniła się od czasu wyszukania. Odśwież dane urządzenia."
            )
        if existing_document_rows:
            raise ValueError("Dokument BNP o podanym numerze został już zapisany w PZ.")
        existing_buyouts = _find_existing_buyout_by_number(
            cursor,
            supplier_id=supplier_id,
            kp_number=source_parsed["number"],
        )
        if existing_buyouts:
            raise ValueError(
                "Dla tego numeru KP istnieje już PZ wykupu BNP. Ponowny zapis został zablokowany."
            )
        if warehouse_item["quantity"] != Decimal("0"):
            raise ValueError(
                "Kartoteka na magazynie 27 ma stan różny od 0. Ponowny wykup został zablokowany."
            )

        cursor.execute(
            """
            SELECT FIRST 1 ID_MAGAZYN_TABLE
            FROM MAGAZYN
            WHERE ID_MAGAZYN = ?
              AND UPPER(TRIM(COALESCE(INDEKS, ''))) = ?
              AND ID_MAGAZYN_TABLE <> ?
            """,
            (
                BNP_BUYOUT_WAREHOUSE_ID,
                normalized_index.upper(),
                warehouse_item_id,
            ),
        )
        if cursor.fetchone() is not None:
            raise ValueError("Na magazynie 27 istnieje już inna kartoteka o podanym indeksie.")

        cursor.execute(
            """
            UPDATE MAGAZYN
            SET
                INDEKS = ?,
                NAZWA = ?,
                VAT_STAWKA = ?,
                IDVAT = COALESCE(IDVAT, 1),
                UWAGI = ?
            WHERE ID_MAGAZYN_TABLE = ?
              AND ID_MAGAZYN = ?
            """,
            (
                normalized_index,
                normalized_name,
                DEFAULT_VAT_RATE,
                _text(f"CTIP WYKUP BNP {normalized_document} {kto}", 1000),
                warehouse_item_id,
                BNP_BUYOUT_WAREHOUSE_ID,
            ),
        )

        document_number = _next_pz_document_number(cursor)
        pz_number = _text(f"PZ / {document_number} / {document_date.year}", 30)
        issued_by_value = _text(issued_by, 100) or "CTIP"
        cursor.execute(
            """
            INSERT INTO ZAKUPY (
                ID_ODDZIAL,
                ID_FIRMA,
                ID_KLIENT,
                NAZWA,
                ADRES,
                KOD,
                POCZTA,
                NIP,
                RODZAJ_DOK,
                DOKUMENT,
                NUMER,
                ID_MP,
                ID_MW,
                DOK_ZEW,
                DATA_WYST,
                DATA_PRZY_WYDA,
                DATA_PLAT,
                PLATNOSC,
                MIEJSCE_WYST,
                WYSTAWIL,
                ODBRUTTO
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING ID_ZAKUPY_TABLE
            """,
            (
                1,
                1,
                supplier_id,
                supplier["name"],
                supplier["address"],
                supplier["postal_code"],
                supplier["city"],
                supplier["nip"],
                "PZ",
                document_number,
                pz_number,
                BNP_BUYOUT_WAREHOUSE_ID,
                0,
                normalized_document,
                document_date,
                document_date,
                document_date,
                DEFAULT_PAYMENT,
                DEFAULT_PLACE,
                issued_by_value,
                0,
            ),
        )
        pz_row = cursor.fetchone()
        if pz_row is None or pz_row[0] is None:
            raise RuntimeError("Nie udało się utworzyć nagłówka PZ wykupu BNP.")
        pz_id = int(pz_row[0])

        quantity = Decimal("1")
        vat_percent = _parse_vat_rate(DEFAULT_VAT_RATE)
        value_net = _money(price_net * quantity)
        value_vat = _money(value_net * vat_percent / Decimal("100"))
        value_gross = _money(value_net + value_vat)
        cursor.execute(
            """
            INSERT INTO ZAKPOZYCJA (
                ID_ZAKUPY,
                ID_FIRMA,
                ID_KLIENT,
                ID_MAGAZYN,
                RODZAJ_DOK,
                NUMER,
                DATA_PRZY_WYDA,
                ID_MP,
                ID_MW,
                RODZAJ,
                INDEKS,
                NAZWA,
                CENA_NETTO,
                ILOSC,
                JM,
                WARTOSC_NETTO,
                CENA_Z,
                WARTOSC_Z,
                STAWKA_VAT,
                VAT,
                IDVAT,
                WARTOSC_BRUTTO,
                POBRANO,
                SERIAL,
                EWIDENCJA
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING ID_ZAKPOZYCJA_TABLE
            """,
            (
                pz_id,
                1,
                supplier_id,
                warehouse_item_id,
                "PZ",
                pz_number,
                document_date,
                BNP_BUYOUT_WAREHOUSE_ID,
                0,
                ITEM_KIND_TOWAR_INNY,
                normalized_index,
                normalized_name,
                price_net,
                quantity,
                DEFAULT_UNIT,
                value_net,
                Decimal("0"),
                Decimal("0"),
                DEFAULT_VAT_RATE,
                value_vat,
                1,
                value_gross,
                Decimal("0"),
                "",
                "",
            ),
        )
        position_row = cursor.fetchone()
        if position_row is None or position_row[0] is None:
            raise RuntimeError("Nie udało się utworzyć pozycji PZ wykupu BNP.")
        zakpozycja_id = int(position_row[0])

        cursor.execute(
            """
            UPDATE MASZYNA
            SET EWIDENCJA = ?
            WHERE ID_MASZYNA_TABLE = ?
            """,
            (normalized_target, machine_table_id),
        )
        cursor.execute(
            """
            SELECT COALESCE(ILOSC, 0)
            FROM MAGAZYN
            WHERE ID_MAGAZYN_TABLE = ?
            """,
            (warehouse_item_id,),
        )
        quantity_row = cursor.fetchone()
        resulting_quantity = Decimal(str((quantity_row[0] if quantity_row else 0) or 0))
        if resulting_quantity != Decimal("1"):
            raise RuntimeError(
                "PZ nie ustawiło oczekiwanego stanu 1 na magazynie 27; transakcja została wycofana."
            )

        connection.commit()
        return BnpBuyoutResult(
            already_completed=False,
            pz_id=pz_id,
            pz_number=pz_number,
            zakpozycja_id=zakpozycja_id,
            warehouse_item_id=warehouse_item_id,
            warehouse_index=normalized_index,
            warehouse_quantity=resulting_quantity,
            machine_id=int(machine["id_maszyna"]),
            machine_table_id=int(machine["id_maszyna_table"]),
            previous_ewidencja=current_ewidencja,
            target_ewidencja=normalized_target,
            supplier_id=supplier_id,
            external_document=normalized_document,
            purchase_price_netto=price_net,
        )
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


__all__ = [
    "BNP_BUYOUT_WAREHOUSE_ID",
    "BNP_SUPPLIER_NIP",
    "BnpBuyoutResult",
    "BnpCatalogResult",
    "complete_bnp_buyout",
    "create_bnp_catalog_item",
    "lookup_bnp_buyout",
]
