"""Operacje zapisu Firebird dla procesu przyjecia urzadzen w module /device."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.services.firebird_runtime import firebird_connection

DEVICE_WAREHOUSE_ID = settings.fb_warehouse_id
WAREHOUSE_OWNER_CLIENT_ID = settings.fb_warehouse_client_id
ITEM_KIND_TOWAR_INNY = "2. Towar inny"
AUTO_INDEX_PREFIX = "AUTO/"
DEFAULT_UNIT = "szt."
DEFAULT_VAT_RATE = "23 %"
DEFAULT_PLACE = "CTIP"
DEFAULT_PAYMENT = "Przelew"
DEFAULT_MACHINE_ODDZIAL_ID = 1
DEFAULT_MACHINE_FIRMA_ID = 1
DEFAULT_MACHINE_IDVAT = 0
DEFAULT_MACHINE_STOI = f"MAGAZYN {settings.fb_warehouse_id}"
DEFAULT_MACHINE_SERVICE_KIND = "Platne"
DEFAULT_EWIDENCJA_PREFIX = "KP/"
DEFAULT_EWIDENCJA_WIDTH = 4
SUPPLIER_TYPE_VALUE = "Dostawca"
SUPPLIER_KIND_VALUE = 4
MODEL_ID_OUTLIER_THRESHOLD = 1_000_000
INTAKE_GENERATOR_GUARDS = (
    ("LOG_ID_LOG_TABLE_GEN", "LOG", "ID_LOG_TABLE"),
    ("MAGAZYN_ID_MAGAZYN_TABLE_GEN", "MAGAZYN", "ID_MAGAZYN_TABLE"),
    ("INS_ID_ZAKUPY_TABLE_GEN", "ZAKUPY", "ID_ZAKUPY_TABLE"),
    ("INS_ID_ZAKPOZ_TABLE_GEN", "ZAKPOZYCJA", "ID_ZAKPOZYCJA_TABLE"),
    ("MASZYNA_ID_MASZYNA_TABLE_GEN", "MASZYNA", "ID_MASZYNA_TABLE"),
    ("MASZYNA_ID_MASZYNA_GEN", "MASZYNA", "ID_MASZYNA"),
    ("SYNCHRO_ID_SYNC_GEN", "SYNCHRO", "ID_SYNC"),
)


@dataclass(slots=True)
class DeviceCatalogSyncResult:
    total_models: int
    created: int
    updated: int
    existing: int
    rows: list[dict[str, Any]]


@dataclass(slots=True)
class DeviceIntakeResult:
    model_id: int
    producer: str
    model: str
    warehouse_item_id: int
    warehouse_index: str
    pz_id: int
    pz_number: str
    zakpozycja_id: int
    serial_id: int | None
    serial: str
    ewidencja: str
    supplier_id: int
    machine_id: int | None = None
    machine_table_id: int | None = None
    purchase_price_netto: Decimal | None = None


@dataclass(slots=True)
class DeviceIntakeItemInput:
    model_id: int
    serial: str
    ewidencja: str | None = None
    purchase_price_netto: Decimal | None = None


@dataclass(slots=True)
class DeviceIntakeBatchResult:
    pz_id: int
    pz_number: str
    supplier_id: int
    items: list[DeviceIntakeResult]


def _firebird_connection():
    """Zachowuje punkt rozszerzenia testów, używając konfiguracji runtime."""
    return firebird_connection()


def _text(value: Any, max_length: int | None = None) -> str:
    normalized = str(value or "").strip()
    if max_length is not None:
        return normalized[:max_length]
    return normalized


def _normalize_device_key(value: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", _text(value).upper())


def _flag_text(value: Any) -> str:
    normalized = _text(value).upper()
    if not normalized:
        return ""
    if normalized in {"1", "TAK", "TRUE", "Y"}:
        return "TAK"
    if normalized in {"0", "NIE", "FALSE", "N"}:
        return "NIE"
    return normalized


def _parse_vat_rate(value: str | None) -> Decimal:
    raw = _text(value)
    if not raw:
        return Decimal("23")
    match = re.search(r"(\d+(?:[.,]\d+)?)", raw)
    if not match:
        return Decimal("23")
    return Decimal(match.group(1).replace(",", "."))


def _money(value: Decimal | int | float | str) -> Decimal:
    return Decimal(value).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _acquire_firebird_write_lock(cursor) -> None:
    """Serializuje numerację PZ, KP i identyfikatorów MODEL w jednej bazie."""
    cursor.execute("SELECT ID_FIRMA FROM FIRMA WHERE ID_FIRMA = 1 WITH LOCK")
    if cursor.fetchone() is None:
        raise RuntimeError("Nie znaleziono rekordu blokady FIRMA.ID_FIRMA=1.")


def _ensure_intake_generators(cursor) -> None:
    """Podnosi opóźnione generatory wymagane przez zapis PZ i jego triggery."""
    for generator_name, table_name, column_name in INTAKE_GENERATOR_GUARDS:
        cursor.execute(f"SELECT GEN_ID({generator_name}, 0) FROM RDB$DATABASE")
        generator_row = cursor.fetchone()
        current_value = int((generator_row[0] if generator_row else 0) or 0)
        cursor.execute(f"SELECT COALESCE(MAX({column_name}), 0) FROM {table_name}")
        maximum_row = cursor.fetchone()
        maximum_value = int((maximum_row[0] if maximum_row else 0) or 0)
        if current_value >= maximum_value:
            continue
        increment = maximum_value - current_value
        cursor.execute(f"SELECT GEN_ID({generator_name}, {increment}) FROM RDB$DATABASE")
        repaired_row = cursor.fetchone()
        repaired_value = int((repaired_row[0] if repaired_row else 0) or 0)
        if repaired_value < maximum_value:
            raise RuntimeError(f"Nie udało się zsynchronizować generatora {generator_name}.")


def _normalize_nip(value: str | None) -> str:
    return re.sub(r"[^0-9]", "", _text(value, 30))


def _normalize_ewidencja_prefix(value: str | None) -> str:
    prefix = _text(value, 50).upper()
    if not prefix:
        prefix = DEFAULT_EWIDENCJA_PREFIX
    if not prefix.endswith("/"):
        prefix = f"{prefix}/"
    return prefix


def _extract_trailing_number(value: str, prefix: str) -> tuple[int | None, int]:
    text_value = _text(value, 100).upper()
    if not text_value.startswith(prefix):
        return None, 0
    suffix = text_value[len(prefix) :]
    match = re.fullmatch(r"(\d+)", suffix)
    if not match:
        return None, 0
    return int(match.group(1)), len(match.group(1))


def _pick_next_from_width_mode(values: list[tuple[int, int]]) -> tuple[int, int] | None:
    if not values:
        return None
    by_width: dict[int, list[int]] = {}
    for number, width in values:
        by_width.setdefault(width, []).append(int(number))

    dominant_width = max(
        by_width,
        key=lambda current_width: (
            len(by_width[current_width]),
            -abs(current_width - DEFAULT_EWIDENCJA_WIDTH),
            -current_width,
        ),
    )
    max_number = max(by_width[dominant_width])
    resolved_width = max(DEFAULT_EWIDENCJA_WIDTH, dominant_width)
    return max_number + 1, resolved_width


def _resolve_next_ewidencja_number(cursor, *, prefix: str) -> tuple[int, int]:
    prefix_value = _normalize_ewidencja_prefix(prefix)
    max_digits = 6
    candidates: list[tuple[int, int]] = []
    sources = (
        ("SERIAL", "EWIDENCJA", "ID_SERIAL"),
        ("MASZYNA", "EWIDENCJA", "ID_MASZYNA_TABLE"),
        ("MAGAZYN", "INDEKS", "ID_MAGAZYN_TABLE"),
        ("ZAKPOZYCJA", "EWIDENCJA", "ID_ZAKPOZYCJA_TABLE"),
    )
    for table_name, value_column, id_column in sources:
        cursor.execute(
            f"""
            SELECT FIRST 4000 {value_column}
            FROM {table_name}
            WHERE UPPER(COALESCE({value_column}, '')) STARTING WITH ?
            ORDER BY {id_column} DESC
            """,
            (prefix_value,),
        )
        for (raw_value,) in cursor.fetchall():
            parsed_value, parsed_width = _extract_trailing_number(
                _text(raw_value, 100),
                prefix_value,
            )
            if parsed_value is None or parsed_width > max_digits:
                continue
            candidates.append((parsed_value, parsed_width))

    picked = _pick_next_from_width_mode(candidates)
    return picked or (1, DEFAULT_EWIDENCJA_WIDTH)


def _build_ewidencja_from_number(prefix: str, number: int, width: int) -> str:
    normalized_prefix = _normalize_ewidencja_prefix(prefix)
    return f"{normalized_prefix}{int(number):0{int(max(1, width))}d}"


def get_next_ewidencja_suggestion(*, prefix: str | None = None) -> dict[str, Any]:
    """Zwraca kolejny numer ewidencyjny wedlug aktualnych danych SERIAL/MASZYNA."""
    connection = _firebird_connection()
    cursor = connection.cursor()
    try:
        normalized_prefix = _normalize_ewidencja_prefix(prefix)
        next_number, width = _resolve_next_ewidencja_number(cursor, prefix=normalized_prefix)
        return {
            "prefix": normalized_prefix,
            "next_number": next_number,
            "width": width,
            "suggested": _build_ewidencja_from_number(normalized_prefix, next_number, width),
        }
    finally:
        cursor.close()
        connection.close()


def _list_models_for_sync(cursor, model_ids: list[int] | None) -> list[tuple[int, str, str]]:
    if model_ids:
        placeholders = ", ".join("?" for _ in model_ids)
        cursor.execute(
            f"""
            SELECT ID_MODEL, MARKA, MODEL
            FROM MODEL
            WHERE ID_MODEL IN ({placeholders})
            ORDER BY ID_MODEL
            """,
            tuple(int(model_id) for model_id in model_ids),
        )
    else:
        cursor.execute(
            """
            SELECT ID_MODEL, MARKA, MODEL
            FROM MODEL
            WHERE TRIM(COALESCE(MARKA, '')) <> ''
              AND TRIM(COALESCE(MODEL, '')) <> ''
            ORDER BY ID_MODEL
            """
        )
    return [
        (int(row[0]), _text(row[1], 50), _text(row[2], 50))
        for row in cursor.fetchall()
        if row and row[0] is not None
    ]


def _find_auto_catalog_item(cursor, model_id: int) -> dict[str, Any] | None:
    cursor.execute(
        """
        SELECT FIRST 1
            ID_MAGAZYN_TABLE,
            INDEKS,
            NAZWA,
            IDVAT,
            VAT_STAWKA
        FROM MAGAZYN
        WHERE COALESCE(ID_MAGAZYN, 0) = ?
          AND COALESCE(ID_MODEL, 0) = ?
          AND UPPER(COALESCE(INDEKS, '')) STARTING WITH ?
        ORDER BY ID_MAGAZYN_TABLE ASC
        """,
        (DEVICE_WAREHOUSE_ID, model_id, AUTO_INDEX_PREFIX),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return {
        "id_magazyn_table": int(row[0]) if row[0] is not None else None,
        "indeks": _text(row[1], 100),
        "nazwa": _text(row[2], 250),
        "idvat": int(row[3]) if row[3] is not None else 1,
        "vat_stawka": _text(row[4], 20) or DEFAULT_VAT_RATE,
    }


def _next_auto_index_number(cursor) -> int:
    cursor.execute(
        """
        SELECT INDEKS
        FROM MAGAZYN
        WHERE COALESCE(ID_MAGAZYN, 0) = ?
          AND UPPER(COALESCE(INDEKS, '')) STARTING WITH ?
        """,
        (DEVICE_WAREHOUSE_ID, AUTO_INDEX_PREFIX),
    )
    max_seq = 0
    for (indeks,) in cursor.fetchall():
        match = re.match(r"^AUTO/(\d+)$", _text(indeks).upper())
        if not match:
            continue
        max_seq = max(max_seq, int(match.group(1)))
    return max_seq + 1


def _format_auto_index(value: int) -> str:
    return f"{AUTO_INDEX_PREFIX}{value:04d}"


def _upsert_auto_catalog_item(
    cursor,
    *,
    model_id: int,
    marka: str,
    model_name: str,
    only_missing: bool,
    next_index_number: int,
    kto: str,
) -> tuple[str, int, str, int]:
    display_name = _text(f"{marka} {model_name}", 250) or f"MODEL {model_id}"
    existing = _find_auto_catalog_item(cursor, model_id)
    if existing is None:
        auto_index = _format_auto_index(next_index_number)
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
                DEVICE_WAREHOUSE_ID,
                ITEM_KIND_TOWAR_INNY,
                auto_index,
                display_name,
                DEFAULT_UNIT,
                0,
                date.today(),
                _text(marka, 50),
                _text(model_name, 50),
                model_id,
                "TAK",
                1,
                DEFAULT_VAT_RATE,
                1,
                "TAK",
                _text(f"AUTO-CATALOG {kto}", 1000),
            ),
        )
        created_id = int(cursor.fetchone()[0])
        return "created", created_id, auto_index, next_index_number + 1

    existing_id = int(existing["id_magazyn_table"])
    if only_missing:
        return "existing", existing_id, _text(existing["indeks"], 100), next_index_number

    cursor.execute(
        """
        UPDATE MAGAZYN
        SET
            RODZAJ = ?,
            NAZWA = ?,
            JM = ?,
            MARKA = ?,
            MODEL = ?,
            ID_MODEL = ?,
            SERIAL = ?,
            IDVAT = COALESCE(IDVAT, 1),
            VAT_STAWKA = COALESCE(NULLIF(TRIM(VAT_STAWKA), ''), ?),
            SYNWP = 1,
            V_2010A = 'TAK',
            UWAGI = ?
        WHERE ID_MAGAZYN_TABLE = ?
        """,
        (
            ITEM_KIND_TOWAR_INNY,
            display_name,
            DEFAULT_UNIT,
            _text(marka, 50),
            _text(model_name, 50),
            model_id,
            "TAK",
            DEFAULT_VAT_RATE,
            _text(f"AUTO-CATALOG {kto}", 1000),
            existing_id,
        ),
    )
    return "updated", existing_id, _text(existing["indeks"], 100), next_index_number


def sync_device_catalog_from_models(
    *,
    model_ids: list[int] | None = None,
    only_missing: bool = True,
    kto: str = "CTIP",
) -> DeviceCatalogSyncResult:
    """Zapewnia kartoteke AUTO na magazynie 28 dla wskazanych modeli."""
    connection = _firebird_connection()
    cursor = connection.cursor()
    try:
        models = _list_models_for_sync(cursor, model_ids)
        next_index_number = _next_auto_index_number(cursor)
        rows: list[dict[str, Any]] = []
        created = 0
        updated = 0
        existing = 0
        for model_id, marka, model_name in models:
            action, warehouse_id, index_value, next_index_number = _upsert_auto_catalog_item(
                cursor,
                model_id=model_id,
                marka=marka,
                model_name=model_name,
                only_missing=only_missing,
                next_index_number=next_index_number,
                kto=kto,
            )
            if action == "created":
                created += 1
            elif action == "updated":
                updated += 1
            else:
                existing += 1
            rows.append(
                {
                    "model_id": model_id,
                    "marka": marka,
                    "model": model_name,
                    "warehouse_item_id": warehouse_id,
                    "index": index_value,
                    "action": action,
                }
            )
        connection.commit()
        return DeviceCatalogSyncResult(
            total_models=len(models),
            created=created,
            updated=updated,
            existing=existing,
            rows=rows,
        )
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


def _find_model(cursor, model_id: int) -> tuple[int, str, str] | None:
    cursor.execute(
        """
        SELECT FIRST 1 ID_MODEL, MARKA, MODEL
        FROM MODEL
        WHERE ID_MODEL = ?
        """,
        (model_id,),
    )
    row = cursor.fetchone()
    if row is None or row[0] is None:
        return None
    return int(row[0]), _text(row[1], 50), _text(row[2], 50)


def _find_model_details(cursor, model_id: int) -> dict[str, Any] | None:
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
        (model_id,),
    )
    row = cursor.fetchone()
    if row is None or row[0] is None:
        return None
    return {
        "id_model": int(row[0]),
        "marka": _text(row[1], 50),
        "model": _text(row[2], 50),
        "grupa": _text(row[3], 50),
        "rodzaj": _text(row[4], 50),
        "kolor": _flag_text(row[5]),
        "plik": _text(row[6], 250),
    }


def _find_supplier(cursor, supplier_id: int) -> dict[str, Any] | None:
    cursor.execute(
        """
        SELECT FIRST 1
            ID_KLIENT,
            NAZWA,
            ADRES,
            KOD,
            POCZTA,
            NIP,
            TELEFON,
            E_MAIL,
            TYP,
            RODZAJ
        FROM KLIENT
        WHERE ID_KLIENT = ?
        """,
        (supplier_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return {
        "id_klient": int(row[0]),
        "nazwa": _text(row[1], 500),
        "adres": _text(row[2], 250),
        "kod": _text(row[3], 6),
        "poczta": _text(row[4], 150),
        "nip": _text(row[5], 30),
        "telefon": _text(row[6], 100),
        "email": _text(row[7], 200),
        "typ": _text(row[8], 50),
        "rodzaj": int(row[9]) if row[9] is not None else None,
    }


def _is_supplier_marker(payload: dict[str, Any]) -> bool:
    supplier_type = _text(payload.get("typ"), 50).upper()
    supplier_kind = payload.get("rodzaj")
    if supplier_type == "DOSTAWCA":
        return True
    return isinstance(supplier_kind, int) and supplier_kind == 4


def _model_id_from_search_query(query: str | None) -> int | None:
    """Odczytuje ID modelu z liczby albo etykiety pola wyboru `ID | opis`."""
    match = re.fullmatch(r"\s*(\d+)(?:\s*\|.*)?\s*", query or "")
    if match is None:
        return None
    model_id = int(match.group(1))
    return model_id if model_id > 0 else None


def _supplier_id_from_search_query(query: str | None) -> int | None:
    """Odczytuje ID dostawcy wyłącznie z pełnej etykiety `ID | opis`."""
    if "|" not in (query or ""):
        return None
    return _model_id_from_search_query(query)


def search_device_models(*, query: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Zwraca liste modeli dla pola wyboru w formularzu przyjecia."""
    safe_limit = max(1, min(int(limit), 500))
    normalized_query = _text(query, 100)
    model_id_query = _model_id_from_search_query(normalized_query)
    connection = _firebird_connection()
    cursor = connection.cursor()
    try:
        where_clause = ""
        params: list[Any] = [DEVICE_WAREHOUSE_ID, AUTO_INDEX_PREFIX]
        if model_id_query is not None:
            where_clause = "WHERE m.ID_MODEL = ?"
            params.append(model_id_query)
        elif normalized_query:
            text_query = normalized_query.upper()
            where_clause = """
                WHERE UPPER(COALESCE(m.MARKA, '')) CONTAINING ?
                   OR UPPER(COALESCE(m.MODEL, '')) CONTAINING ?
                   OR CAST(m.ID_MODEL AS VARCHAR(100)) CONTAINING ?
            """
            params.extend((text_query, text_query, normalized_query))
        cursor.execute(
            f"""
            SELECT FIRST {safe_limit}
                m.ID_MODEL,
                m.MARKA,
                m.MODEL,
                m.GRUPA,
                m.RODZAJ,
                m.KOLOR,
                m.PLIK,
                (
                    SELECT FIRST 1 mg.ID_MAGAZYN_TABLE
                    FROM MAGAZYN mg
                    WHERE COALESCE(mg.ID_MAGAZYN, 0) = ?
                      AND COALESCE(mg.ID_MODEL, 0) = m.ID_MODEL
                      AND UPPER(COALESCE(mg.INDEKS, '')) STARTING WITH ?
                ) AS AUTO_ITEM_ID
            FROM MODEL m
            {where_clause}
            ORDER BY m.MARKA, m.MODEL, m.ID_MODEL
            """,
            tuple(params),
        )
        result: list[dict[str, Any]] = []
        for row in cursor.fetchall():
            if row[0] is None:
                continue
            result.append(
                {
                    "id_model": int(row[0]),
                    "marka": _text(row[1], 50),
                    "model": _text(row[2], 50),
                    "grupa": _text(row[3], 50),
                    "rodzaj": _text(row[4], 50),
                    "kolor": _flag_text(row[5]),
                    "plik": _text(row[6], 250),
                    "auto_item_id": int(row[7]) if row[7] is not None else None,
                }
            )
        return result
    finally:
        cursor.close()
        connection.close()


def search_device_suppliers(*, query: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Zwraca liste dostawcow dla pola wyboru w formularzu przyjecia."""
    safe_limit = max(1, min(int(limit), 500))
    normalized_query = _text(query, 100)
    supplier_id_query = _supplier_id_from_search_query(normalized_query)
    connection = _firebird_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT DISTINCT ID_KLIENT
            FROM ZAKUPY
            WHERE RODZAJ_DOK = 'PZ'
              AND ID_KLIENT IS NOT NULL
            """
        )
        pz_supplier_ids = {int(row[0]) for row in cursor.fetchall() if row and row[0] is not None}

        if supplier_id_query is not None:
            cursor.execute(
                """
                SELECT
                    ID_KLIENT,
                    NAZWA,
                    NIP,
                    ADRES,
                    KOD,
                    POCZTA,
                    TELEFON,
                    E_MAIL,
                    TYP,
                    RODZAJ
                FROM KLIENT K
                WHERE ID_KLIENT = ?
                ORDER BY NAZWA, ID_KLIENT
                """,
                (supplier_id_query,),
            )
        elif normalized_query:
            text_query = normalized_query.upper()
            nip_query = _normalize_nip(normalized_query)
            name_query = text_query[:500]
            address_query = text_query[:250]
            city_query = text_query[:150]
            nip_filter = nip_query[:20]
            sql = """
                SELECT
                    ID_KLIENT,
                    NAZWA,
                    NIP,
                    ADRES,
                    KOD,
                    POCZTA,
                    TELEFON,
                    E_MAIL,
                    TYP,
                    RODZAJ
                FROM KLIENT K
                WHERE (
                    UPPER(COALESCE(NAZWA, '')) CONTAINING ?
                    OR UPPER(COALESCE(ADRES, '')) CONTAINING ?
                    OR UPPER(COALESCE(POCZTA, '')) CONTAINING ?
            """
            params: list[str] = [name_query, address_query, city_query]
            if nip_filter:
                sql += "\n                      OR UPPER(COALESCE(NIP, '')) CONTAINING ?"
                params.append(nip_filter)
            sql += """
                  )
                ORDER BY NAZWA, ID_KLIENT
            """
            cursor.execute(sql, tuple(params))
        else:
            cursor.execute(
                """
                SELECT
                    ID_KLIENT,
                    NAZWA,
                    NIP,
                    ADRES,
                    KOD,
                    POCZTA,
                    TELEFON,
                    E_MAIL,
                    TYP,
                    RODZAJ
                FROM KLIENT K
                ORDER BY NAZWA, ID_KLIENT
                """
            )

        result: list[dict[str, Any]] = []
        for row in cursor.fetchall():
            if row[0] is None:
                continue
            supplier_id = int(row[0])
            supplier = {
                "id_klient": supplier_id,
                "nazwa": _text(row[1], 500),
                "nip": _text(row[2], 30),
                "adres": _text(row[3], 250),
                "kod": _text(row[4], 6),
                "poczta": _text(row[5], 150),
                "telefon": _text(row[6], 100),
                "email": _text(row[7], 200),
                "typ": _text(row[8], 50),
                "rodzaj": int(row[9]) if row[9] is not None else None,
                "used_on_pz": supplier_id in pz_supplier_ids,
            }
            if not _is_supplier_marker(supplier) and supplier_id not in pz_supplier_ids:
                continue
            result.append(supplier)
            if len(result) >= safe_limit:
                break
        return result
    finally:
        cursor.close()
        connection.close()


def create_device_supplier(
    *,
    name: str,
    nip: str | None = None,
    address: str | None = None,
    postal_code: str | None = None,
    city: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    kto: str = "CTIP",
) -> dict[str, Any]:
    """Tworzy podstawowego dostawce w KLIENT i zwraca jego dane."""
    name_value = _text(name, 500)
    if not name_value:
        raise ValueError("Pole nazwa dostawcy jest wymagane.")
    nip_value = _normalize_nip(nip)

    connection = _firebird_connection()
    cursor = connection.cursor()
    try:
        _acquire_firebird_write_lock(cursor)
        if nip_value:
            cursor.execute(
                """
                SELECT FIRST 1 ID_KLIENT
                FROM KLIENT
                WHERE REPLACE(REPLACE(REPLACE(COALESCE(NIP, ''), '-', ''), ' ', ''), '.', '') = ?
                ORDER BY ID_KLIENT DESC
                """,
                (nip_value,),
            )
            row = cursor.fetchone()
            if row and row[0] is not None:
                existing = _find_supplier(cursor, int(row[0]))
                if existing:
                    return existing
        else:
            cursor.execute(
                """
                SELECT FIRST 1 ID_KLIENT
                FROM KLIENT
                WHERE UPPER(TRIM(COALESCE(NAZWA, ''))) = UPPER(TRIM(?))
                  AND UPPER(TRIM(COALESCE(KOD, ''))) = UPPER(TRIM(?))
                  AND UPPER(TRIM(COALESCE(POCZTA, ''))) = UPPER(TRIM(?))
                ORDER BY ID_KLIENT DESC
                """,
                (
                    name_value,
                    _text(postal_code, 6),
                    _text(city, 150),
                ),
            )
            row = cursor.fetchone()
            if row and row[0] is not None:
                raise ValueError(
                    "Istnieje klient bez NIP o tej samej nazwie i lokalizacji. "
                    f"Wybierz istniejący rekord ID_KLIENT={int(row[0])}."
                )

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
                TYP,
                RODZAJ,
                UWAGI,
                AKTYWNY,
                KTO
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING ID_KLIENT
            """,
            (
                1,
                0,
                name_value,
                _text(address, 250),
                _text(postal_code, 6),
                _text(city, 150),
                nip_value,
                _text(phone, 100),
                _text(email, 200),
                SUPPLIER_TYPE_VALUE,
                SUPPLIER_KIND_VALUE,
                _text(f"CTIP-DEVICE SUPPLIER {kto}", 1000),
                "TAK",
                _text(kto, 50),
            ),
        )
        row = cursor.fetchone()
        if row is None or row[0] is None:
            raise RuntimeError("Nie udalo sie utworzyc dostawcy.")
        supplier_id = int(row[0])
        supplier = _find_supplier(cursor, supplier_id)
        if supplier is None:
            raise RuntimeError("Dostawca zapisany, ale nie udalo sie odczytac rekordu.")
        connection.commit()
        return supplier
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


def _find_model_by_signature(cursor, *, marka: str, model_name: str) -> dict[str, Any] | None:
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
        WHERE UPPER(TRIM(COALESCE(MARKA, ''))) = UPPER(TRIM(?))
          AND UPPER(TRIM(COALESCE(MODEL, ''))) = UPPER(TRIM(?))
        ORDER BY ID_MODEL DESC
        """,
        (marka, model_name),
    )
    row = cursor.fetchone()
    if row is None or row[0] is None:
        return None
    return {
        "id_model": int(row[0]),
        "marka": _text(row[1], 50),
        "model": _text(row[2], 50),
        "grupa": _text(row[3], 50),
        "rodzaj": _text(row[4], 50),
        "kolor": _flag_text(row[5]),
        "plik": _text(row[6], 250),
    }


def _next_model_id(cursor) -> int:
    cursor.execute("SELECT ID_MODEL FROM MODEL WHERE ID_MODEL IS NOT NULL")
    values = [int(row[0]) for row in cursor.fetchall() if row and row[0] is not None]
    return _resolve_next_model_id_from_values(values)


def _resolve_next_model_id_from_values(values: list[int]) -> int:
    if not values:
        return 1
    stable_values = [int(value) for value in values if int(value) < MODEL_ID_OUTLIER_THRESHOLD]
    if stable_values:
        return max(stable_values) + 1
    return max(int(value) for value in values) + 1


def load_device_model_taxonomy(*, limit: int = 300) -> dict[str, list[str]]:
    """Zwraca slowniki wyboru `GRUPA` i `RODZAJ` z tabeli MODEL."""
    safe_limit = max(10, min(int(limit), 1000))
    connection = _firebird_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            f"""
            SELECT FIRST {safe_limit} DISTINCT TRIM(COALESCE(GRUPA, ''))
            FROM MODEL
            WHERE TRIM(COALESCE(GRUPA, '')) <> ''
            ORDER BY 1
            """
        )
        groups = [_text(row[0], 50) for row in cursor.fetchall() if _text(row[0], 50)]
        cursor.execute(
            f"""
            SELECT FIRST {safe_limit} DISTINCT TRIM(COALESCE(RODZAJ, ''))
            FROM MODEL
            WHERE TRIM(COALESCE(RODZAJ, '')) <> ''
            ORDER BY 1
            """
        )
        kinds = [_text(row[0], 50) for row in cursor.fetchall() if _text(row[0], 50)]
        return {"groups": groups, "kinds": kinds}
    finally:
        cursor.close()
        connection.close()


def _normalize_model_name_for_brand(marka: str, model_name: str) -> str:
    brand_upper = _text(marka, 50).upper()
    raw_model = _text(model_name, 50)
    if brand_upper != "RICOH":
        return raw_model
    normalized = re.sub(r"\s+", "", raw_model).upper()
    match = re.match(r"^IMC(\d+[A-Z0-9]*)$", normalized)
    if match:
        return _text(f"IM C{match.group(1)}", 50)
    return raw_model.upper() if normalized else raw_model


def create_device_model(
    *,
    marka: str,
    model_name: str,
    grupa: str | None = None,
    rodzaj: str | None = None,
    kolor: bool | None = None,
    plik: str | None = None,
    kto: str = "CTIP",
) -> dict[str, Any]:
    """Tworzy kompletny rekord MODEL bez wspólnej kartoteki magazynowej."""
    marka_value = _text(marka, 50)
    model_value = _normalize_model_name_for_brand(marka_value, model_name)
    grupa_value = _text(grupa, 50)
    rodzaj_value = _text(rodzaj, 50)
    if not marka_value:
        raise ValueError("Pole marka jest wymagane.")
    if not model_value:
        raise ValueError("Pole model jest wymagane.")
    if not grupa_value:
        raise ValueError("Pole grupa jest wymagane.")
    if not rodzaj_value:
        raise ValueError("Pole rodzaj jest wymagane.")
    if kolor is None:
        raise ValueError("Wybierz, czy model jest kolorowy.")

    connection = _firebird_connection()
    cursor = connection.cursor()
    try:
        _acquire_firebird_write_lock(cursor)
        existing = _find_model_by_signature(
            cursor,
            marka=marka_value,
            model_name=model_value,
        )
        created = False
        if existing is not None:
            model_id = int(existing["id_model"])
            _validate_model_for_intake(existing, row_no=1)
            result_model = existing
        else:
            model_id = _next_model_id(cursor)
            cursor.execute(
                """
                INSERT INTO MODEL (
                    ID_MODEL,
                    MARKA,
                    MODEL,
                    GRUPA,
                    RODZAJ,
                    KOLOR,
                    PLIK,
                    INNE3
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    model_id,
                    marka_value,
                    model_value,
                    grupa_value,
                    rodzaj_value,
                    "TAK" if kolor else "NIE",
                    _text(plik, 250),
                    _text(f"CTIP-DEVICE MODEL {kto}", 50),
                ),
            )
            created = True
            result_model = {
                "id_model": model_id,
                "marka": marka_value,
                "model": model_value,
                "grupa": grupa_value,
                "rodzaj": rodzaj_value,
                "kolor": "TAK" if kolor else "NIE",
                "plik": _text(plik, 250),
            }

        connection.commit()
        return {
            "created": created,
            "id_model": model_id,
            "marka": result_model["marka"],
            "model": result_model["model"],
            "grupa": result_model["grupa"],
            "rodzaj": result_model["rodzaj"],
            "kolor": _flag_text(result_model["kolor"]) == "TAK",
            "plik": result_model["plik"] or None,
            "image_warning": not bool(result_model["plik"]),
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


def _next_pz_document_number(cursor, *, year: int) -> int:
    cursor.execute(
        """
        SELECT COALESCE(MAX(DOKUMENT), 0)
        FROM ZAKUPY
        WHERE RODZAJ_DOK = 'PZ'
          AND EXTRACT(YEAR FROM DATA_WYST) = ?
        """,
        (int(year),),
    )
    current = cursor.fetchone()
    return int((current[0] if current else 0) or 0) + 1


def _find_machine_conflict(cursor, serial_key: str, ewidencja_key: str) -> dict[str, Any] | None:
    cursor.execute(
        """
        SELECT FIRST 1
            ID_MASZYNA,
            ID_MASZYNA_TABLE,
            SERIAL,
            SERIAL2,
            EWIDENCJA
        FROM MASZYNA
        WHERE UPPER(REPLACE(REPLACE(REPLACE(COALESCE(SERIAL, ''), '/', ''), '-', ''), ' ', '')) = ?
           OR UPPER(REPLACE(REPLACE(REPLACE(COALESCE(SERIAL2, ''), '/', ''), '-', ''), ' ', '')) = ?
           OR UPPER(REPLACE(REPLACE(REPLACE(COALESCE(EWIDENCJA, ''), '/', ''), '-', ''), ' ', '')) = ?
        ORDER BY ID_MASZYNA DESC
        """,
        (serial_key, serial_key, ewidencja_key),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return {
        "id_maszyna": int(row[0]) if row[0] is not None else None,
        "id_maszyna_table": int(row[1]) if row[1] is not None else None,
        "serial": _text(row[2], 100),
        "serial2": _text(row[3], 100),
        "ewidencja": _text(row[4], 100),
    }


def _find_serial_conflict(cursor, serial_key: str, ewidencja_key: str) -> dict[str, Any] | None:
    cursor.execute(
        """
        SELECT FIRST 1
            ID_SERIAL,
            ID_PZ,
            SERIAL,
            EWIDENCJA
        FROM SERIAL
        WHERE UPPER(REPLACE(REPLACE(REPLACE(COALESCE(SERIAL, ''), '/', ''), '-', ''), ' ', '')) = ?
           OR UPPER(REPLACE(REPLACE(REPLACE(COALESCE(EWIDENCJA, ''), '/', ''), '-', ''), ' ', '')) = ?
        ORDER BY ID_SERIAL DESC
        """,
        (serial_key, ewidencja_key),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return {
        "id_serial": int(row[0]) if row[0] is not None else None,
        "id_pz": int(row[1]) if row[1] is not None else None,
        "serial": _text(row[2], 100),
        "ewidencja": _text(row[3], 100),
    }


def _find_inventory_conflict(
    cursor,
    *,
    serial_value: str,
    serial_key: str,
    ewidencja_key: str,
) -> dict[str, Any] | None:
    cursor.execute(
        """
        SELECT FIRST 1
            ID_MAGAZYN_TABLE,
            INDEKS,
            NAZWA
        FROM MAGAZYN
        WHERE UPPER(REPLACE(REPLACE(REPLACE(COALESCE(INDEKS, ''), '/', ''), '-', ''), ' ', '')) = ?
           OR UPPER(COALESCE(NAZWA, '')) CONTAINING ?
        ORDER BY ID_MAGAZYN_TABLE DESC
        """,
        (ewidencja_key, _text(serial_value, 100).upper()),
    )
    row = cursor.fetchone()
    if row is not None:
        return {
            "table": "MAGAZYN",
            "id": int(row[0]),
            "ewidencja": _text(row[1], 100),
            "name": _text(row[2], 500),
        }

    cursor.execute(
        """
        SELECT FIRST 1
            ID_ZAKPOZYCJA_TABLE,
            SERIAL,
            EWIDENCJA
        FROM ZAKPOZYCJA
        WHERE UPPER(REPLACE(REPLACE(REPLACE(COALESCE(SERIAL, ''), '/', ''), '-', ''), ' ', '')) = ?
           OR UPPER(REPLACE(REPLACE(REPLACE(COALESCE(EWIDENCJA, ''), '/', ''), '-', ''), ' ', '')) = ?
        ORDER BY ID_ZAKPOZYCJA_TABLE DESC
        """,
        (serial_key, ewidencja_key),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return {
        "table": "ZAKPOZYCJA",
        "id": int(row[0]),
        "serial": _text(row[1], 100),
        "ewidencja": _text(row[2], 100),
    }


def _validate_model_for_intake(model_details: dict[str, Any], *, row_no: int) -> None:
    missing = [
        label
        for key, label in (
            ("marka", "MARKA"),
            ("model", "MODEL"),
            ("grupa", "GRUPA"),
            ("rodzaj", "RODZAJ"),
            ("kolor", "KOLOR"),
        )
        if not _text(model_details.get(key), 100)
    ]
    if missing:
        raise ValueError(
            f"Wiersz {row_no}: model ID={model_details.get('id_model')} "
            f"nie ma wymaganych pól: {', '.join(missing)}."
        )


def _insert_warehouse_unit(
    cursor,
    *,
    model_details: dict[str, Any],
    serial_value: str,
    ewidencja_value: str,
    purchase_price_net: Decimal,
    marker: str,
) -> int:
    display_name = _text(
        f"{model_details['marka']} {model_details['model']} S/N:{serial_value}",
        500,
    )
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
            CENA_Z1,
            SYNWP,
            V_2010A,
            UWAGI
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING ID_MAGAZYN_TABLE
        """,
        (
            1,
            1,
            DEVICE_WAREHOUSE_ID,
            ITEM_KIND_TOWAR_INNY,
            _text(ewidencja_value, 100),
            display_name,
            DEFAULT_UNIT,
            Decimal("0"),
            date.today(),
            _text(model_details["marka"], 50),
            _text(model_details["model"], 50),
            int(model_details["id_model"]),
            "NIE",
            1,
            DEFAULT_VAT_RATE,
            purchase_price_net,
            1,
            "TAK",
            _text(marker, 1000),
        ),
    )
    row = cursor.fetchone()
    if row is None or row[0] is None:
        raise RuntimeError("Nie udało się utworzyć fizycznej kartoteki MAGAZYN.")
    return int(row[0])


def _insert_machine_row(
    cursor,
    *,
    model_details: dict[str, Any],
    serial_value: str,
    ewidencja_value: str,
    issued_by_value: str,
    pz_number: str,
    kto: str,
) -> tuple[int, int]:
    marka = _text(model_details.get("marka"), 50)
    model_name = _text(model_details.get("model"), 50)
    grupa = _text(model_details.get("grupa"), 50) or "Druk"
    rodzaj = _text(model_details.get("rodzaj"), 50) or DEFAULT_MACHINE_SERVICE_KIND
    kolorowa = "TAK" if _flag_text(model_details.get("kolor")) == "TAK" else "NIE"
    uwagi = _text(f"AUTO-INTAKE {pz_number} {kto}", 1000)

    cursor.execute(
        """
        INSERT INTO MASZYNA (
            ID_ODDZIAL,
            ID_FIRMA,
            ID_KLIENT,
            ID_MODEL,
            MARKA,
            MODEL,
            GRUPA,
            SERIAL,
            SERIAL2,
            EWIDENCJA,
            STOI,
            UWAGI,
            TYP,
            TECHNIK,
            RODZAJ_US,
            IDVAT,
            AKTYWNA,
            KOLOROWA,
            SYNWP,
            V_2010A
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING ID_MASZYNA, ID_MASZYNA_TABLE
        """,
        (
            DEFAULT_MACHINE_ODDZIAL_ID,
            DEFAULT_MACHINE_FIRMA_ID,
            WAREHOUSE_OWNER_CLIENT_ID,
            int(model_details["id_model"]),
            marka,
            model_name,
            grupa,
            serial_value,
            None,
            ewidencja_value,
            DEFAULT_MACHINE_STOI,
            uwagi,
            rodzaj,
            issued_by_value,
            rodzaj,
            DEFAULT_MACHINE_IDVAT,
            "TAK",
            kolorowa,
            1,
            "TAK",
        ),
    )
    row = cursor.fetchone()
    if row is None or row[0] is None or row[1] is None:
        raise RuntimeError("Nie udalo sie utworzyc rekordu MASZYNA.")
    return int(row[0]), int(row[1])


def _prepare_batch_items(items: list[DeviceIntakeItemInput]) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        serial_value = _text(item.serial, 100)
        ewidencja_value = _text(item.ewidencja, 100)
        serial_key = _normalize_device_key(serial_value)
        ewidencja_key = _normalize_device_key(ewidencja_value)
        if not serial_key:
            raise ValueError(f"Wiersz {index}: pole serial jest wymagane.")
        price_netto_value = (
            _money(item.purchase_price_netto)
            if item.purchase_price_netto is not None
            else Decimal("0")
        )
        if price_netto_value < Decimal("0"):
            raise ValueError(f"Wiersz {index}: cena zakupu nie moze byc ujemna.")
        prepared.append(
            {
                "row_no": index,
                "model_id": int(item.model_id),
                "serial": serial_value,
                "serial_key": serial_key,
                "ewidencja": ewidencja_value,
                "ewidencja_key": ewidencja_key,
                "purchase_price_netto": price_netto_value,
            }
        )
    return prepared


def _autofill_missing_ewidencja(
    cursor,
    *,
    prepared_items: list[dict[str, Any]],
    ewidencja_prefix: str | None,
) -> None:
    missing_rows = [item for item in prepared_items if not item["ewidencja_key"]]
    if not missing_rows:
        return
    prefix = _normalize_ewidencja_prefix(ewidencja_prefix)
    next_number, width = _resolve_next_ewidencja_number(cursor, prefix=prefix)
    for offset, item in enumerate(missing_rows):
        resolved = _build_ewidencja_from_number(prefix, next_number + offset, width)
        item["ewidencja"] = resolved
        item["ewidencja_key"] = _normalize_device_key(resolved)


def _validate_batch_duplicates(prepared_items: list[dict[str, Any]]) -> None:
    seen_serial: dict[str, int] = {}
    seen_ewidencja: dict[str, int] = {}
    for item in prepared_items:
        serial_key = item["serial_key"]
        ewidencja_key = item["ewidencja_key"]
        row_no = int(item["row_no"])
        if serial_key in seen_serial:
            first_row = seen_serial[serial_key]
            raise ValueError(f"Duplikat serial w formularzu: wiersz {first_row} i {row_no}.")
        seen_serial[serial_key] = row_no
        if ewidencja_key in seen_ewidencja:
            first_row = seen_ewidencja[ewidencja_key]
            raise ValueError(
                f"Duplikat numeru ewidencyjnego w formularzu: wiersz {first_row} i {row_no}."
            )
        seen_ewidencja[ewidencja_key] = row_no


def _load_intake_by_marker(cursor, *, marker: str) -> DeviceIntakeBatchResult | None:
    """Odtwarza wynik zatwierdzonego PZ po znaczniku idempotencji."""
    cursor.execute(
        """
        SELECT FIRST 1 ID_ZAKUPY_TABLE, NUMER, ID_KLIENT
        FROM ZAKUPY
        WHERE UWAGI = ?
        ORDER BY ID_ZAKUPY_TABLE DESC
        """,
        (_text(marker, 1000),),
    )
    header = cursor.fetchone()
    if header is None:
        return None
    pz_id = int(header[0])
    pz_number = _text(header[1], 30)
    supplier_id = int(header[2])
    cursor.execute(
        """
        SELECT
            ZP.ID_ZAKPOZYCJA_TABLE,
            ZP.ID_MAGAZYN,
            ZP.SERIAL,
            ZP.EWIDENCJA,
            ZP.CENA_Z,
            M.ID_MODEL,
            M.MARKA,
            M.MODEL,
            M.INDEKS
        FROM ZAKPOZYCJA ZP
        JOIN MAGAZYN M ON M.ID_MAGAZYN_TABLE = ZP.ID_MAGAZYN
        WHERE ZP.ID_ZAKUPY = ?
        ORDER BY ZP.ID_ZAKPOZYCJA_TABLE
        """,
        (pz_id,),
    )
    items: list[DeviceIntakeResult] = []
    for row in cursor.fetchall():
        serial = _text(row[2], 100)
        ewidencja = _text(row[3], 100)
        machine = _find_machine_conflict(
            cursor,
            _normalize_device_key(serial),
            _normalize_device_key(ewidencja),
        )
        items.append(
            DeviceIntakeResult(
                model_id=int(row[5]),
                producer=_text(row[6], 50),
                model=_text(row[7], 50),
                warehouse_item_id=int(row[1]),
                warehouse_index=_text(row[8], 100),
                pz_id=pz_id,
                pz_number=pz_number,
                zakpozycja_id=int(row[0]),
                serial_id=None,
                serial=serial,
                ewidencja=ewidencja,
                supplier_id=supplier_id,
                machine_id=machine.get("id_maszyna") if machine else None,
                machine_table_id=machine.get("id_maszyna_table") if machine else None,
                purchase_price_netto=_money(row[4] or Decimal("0")),
            )
        )
    return DeviceIntakeBatchResult(
        pz_id=pz_id,
        pz_number=pz_number,
        supplier_id=supplier_id,
        items=items,
    )


def create_device_intake_batch(
    *,
    items: list[DeviceIntakeItemInput],
    supplier_id: int | None,
    external_document: str | None = None,
    issued_by: str,
    ewidencja_prefix: str | None = None,
    idempotency_key: str | None = None,
    allow_exception: bool = False,
    exception_reason: str | None = None,
    kto: str = "CTIP",
) -> DeviceIntakeBatchResult:
    """Tworzy atomowy PZ z osobną kartoteką MAGAZYN i MASZYNA dla każdego egzemplarza."""
    if not items:
        raise ValueError("Lista pozycji przyjęcia jest pusta.")
    if supplier_id is None:
        raise ValueError("Wybór dostawcy jest wymagany.")
    issued_by_value = _text(issued_by, 100)
    if not issued_by_value:
        raise ValueError("Użytkownik CTIP nie ma aktywnego powiązania z użytkownikiem MS.")
    external_number = _text(external_document, 30)
    exception_reason_value = _text(exception_reason, 1000)
    if not external_number and not allow_exception:
        raise ValueError("Numer zewnętrzny dokumentu jest wymagany.")
    if allow_exception and len(exception_reason_value) < 10:
        raise ValueError("Uzasadnienie wyjątku musi mieć co najmniej 10 znaków.")

    resolved_supplier_id = int(supplier_id)
    connection = _firebird_connection()
    cursor = connection.cursor()
    try:
        _acquire_firebird_write_lock(cursor)
        _ensure_intake_generators(cursor)
        marker = _text(
            f"CTIP-DEVICE:{idempotency_key}" if idempotency_key else f"CTIP-DEVICE:{kto}",
            1000,
        )
        if idempotency_key:
            replayed = _load_intake_by_marker(cursor, marker=marker)
            if replayed is not None:
                connection.rollback()
                return replayed

        prepared_items = _prepare_batch_items(items)
        _autofill_missing_ewidencja(
            cursor,
            prepared_items=prepared_items,
            ewidencja_prefix=ewidencja_prefix,
        )
        for item in prepared_items:
            if not item["ewidencja_key"]:
                raise ValueError(f"Wiersz {item['row_no']}: pole ewidencja (KP) jest wymagane.")
        _validate_batch_duplicates(prepared_items)

        supplier = _find_supplier(cursor, resolved_supplier_id)
        if supplier is None:
            raise ValueError(f"Nie znaleziono dostawcy ID_KLIENT={resolved_supplier_id}.")

        for item in prepared_items:
            if item["purchase_price_netto"] <= Decimal("0") and not allow_exception:
                raise ValueError(
                    f"Wiersz {item['row_no']}: cena zakupu netto musi być większa od zera."
                )

        today = datetime.now(ZoneInfo("Europe/Warsaw")).date()
        document_number = _next_pz_document_number(cursor, year=today.year)
        pz_number = _text(f"PZ / {document_number} / {today.year}", 30)

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
                ODBRUTTO,
                NETTO1,
                VAT1,
                BRUTTO1,
                SUMA_NETTO,
                SUMA_VAT,
                SUMA_BRUTTO,
                UWAGI
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING ID_ZAKUPY_TABLE
            """,
            (
                1,
                1,
                resolved_supplier_id,
                supplier["nazwa"],
                supplier["adres"],
                supplier["kod"],
                supplier["poczta"],
                supplier["nip"],
                "PZ",
                document_number,
                pz_number,
                DEVICE_WAREHOUSE_ID,
                0,
                external_number,
                today,
                today,
                today,
                DEFAULT_PAYMENT,
                DEFAULT_PLACE,
                issued_by_value,
                Decimal("0"),
                Decimal("0"),
                Decimal("0"),
                Decimal("0"),
                Decimal("0"),
                Decimal("0"),
                Decimal("0"),
                marker,
            ),
        )
        pz_id = int(cursor.fetchone()[0])

        created_items: list[DeviceIntakeResult] = []
        total_net = Decimal("0")
        total_vat = Decimal("0")
        total_gross = Decimal("0")

        for item in prepared_items:
            model_details = _find_model_details(cursor, int(item["model_id"]))
            if model_details is None:
                raise ValueError(f"Nie znaleziono MODEL.ID_MODEL={item['model_id']}.")
            _validate_model_for_intake(model_details, row_no=int(item["row_no"]))

            serial_conflict = _find_serial_conflict(
                cursor,
                str(item["serial_key"]),
                str(item["ewidencja_key"]),
            )
            if serial_conflict is not None:
                raise ValueError(
                    "Wykryto historyczny duplikat serialu lub KP w tabeli SERIAL. "
                    f"ID_SERIAL={serial_conflict['id_serial']}, ID_PZ={serial_conflict['id_pz']}."
                )
            machine_conflict = _find_machine_conflict(
                cursor,
                str(item["serial_key"]),
                str(item["ewidencja_key"]),
            )
            if machine_conflict is not None:
                raise ValueError(
                    "Wykryto istniejący wpis serialu lub KP w tabeli MASZYNA. "
                    f"ID_MASZYNA={machine_conflict['id_maszyna']}."
                )
            inventory_conflict = _find_inventory_conflict(
                cursor,
                serial_value=str(item["serial"]),
                serial_key=str(item["serial_key"]),
                ewidencja_key=str(item["ewidencja_key"]),
            )
            if inventory_conflict is not None:
                raise ValueError(
                    "Wykryto istniejący wpis serialu lub KP w "
                    f"{inventory_conflict['table']} ID={inventory_conflict['id']}."
                )

            price_net = _money(item["purchase_price_netto"])
            warehouse_item_id = _insert_warehouse_unit(
                cursor,
                model_details=model_details,
                serial_value=str(item["serial"]),
                ewidencja_value=str(item["ewidencja"]),
                purchase_price_net=price_net,
                marker=marker,
            )
            warehouse_index = str(item["ewidencja"])
            vat_rate_text = DEFAULT_VAT_RATE
            vat_percent = _parse_vat_rate(vat_rate_text)
            id_vat = 1

            quantity = Decimal("1")
            value_net = _money(price_net * quantity)
            value_vat = _money(value_net * vat_percent / Decimal("100"))
            value_gross = _money(value_net + value_vat)
            total_net += value_net
            total_vat += value_vat
            total_gross += value_gross

            display_name = (
                _text(
                    f"{model_details['marka']} {model_details['model']} S/N:{item['serial']}",
                    500,
                )
                or f"MODEL {model_details['id_model']}"
            )

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
                    EWIDENCJA,
                    UWAGI
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING ID_ZAKPOZYCJA_TABLE
                """,
                (
                    pz_id,
                    1,
                    resolved_supplier_id,
                    warehouse_item_id,
                    "PZ",
                    pz_number,
                    today,
                    DEVICE_WAREHOUSE_ID,
                    0,
                    ITEM_KIND_TOWAR_INNY,
                    warehouse_index,
                    display_name,
                    price_net,
                    quantity,
                    DEFAULT_UNIT,
                    value_net,
                    price_net,
                    value_net,
                    vat_rate_text,
                    value_vat,
                    id_vat,
                    value_gross,
                    Decimal("0"),
                    item["serial"],
                    item["ewidencja"],
                    marker,
                ),
            )
            zakpozycja_id = int(cursor.fetchone()[0])

            machine_id, machine_table_id = _insert_machine_row(
                cursor,
                model_details=model_details,
                serial_value=str(item["serial"]),
                ewidencja_value=str(item["ewidencja"]),
                issued_by_value=issued_by_value,
                pz_number=pz_number,
                kto=kto,
            )

            created_items.append(
                DeviceIntakeResult(
                    model_id=int(model_details["id_model"]),
                    producer=str(model_details["marka"]),
                    model=str(model_details["model"]),
                    warehouse_item_id=warehouse_item_id,
                    warehouse_index=warehouse_index,
                    pz_id=pz_id,
                    pz_number=pz_number,
                    zakpozycja_id=zakpozycja_id,
                    serial_id=None,
                    serial=str(item["serial"]),
                    ewidencja=str(item["ewidencja"]),
                    supplier_id=resolved_supplier_id,
                    machine_id=machine_id,
                    machine_table_id=machine_table_id,
                    purchase_price_netto=_money(item["purchase_price_netto"]),
                )
            )

        total_net = _money(total_net)
        total_vat = _money(total_vat)
        total_gross = _money(total_gross)
        cursor.execute(
            """
            UPDATE ZAKUPY
            SET
                NETTO1 = ?,
                VAT1 = ?,
                BRUTTO1 = ?,
                SUMA_NETTO = ?,
                SUMA_VAT = ?,
                SUMA_BRUTTO = ?,
                ODBRUTTO = ?
            WHERE ID_ZAKUPY_TABLE = ?
            """,
            (
                total_net,
                total_vat,
                total_gross,
                total_net,
                total_vat,
                total_gross,
                total_gross,
                pz_id,
            ),
        )
        connection.commit()
        return DeviceIntakeBatchResult(
            pz_id=pz_id,
            pz_number=pz_number,
            supplier_id=resolved_supplier_id,
            items=created_items,
        )
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


def create_device_intake(
    *,
    model_id: int,
    serial: str,
    ewidencja: str,
    supplier_id: int | None,
    external_document: str | None = None,
    issued_by: str,
    purchase_price_netto: Decimal | None = None,
    idempotency_key: str | None = None,
    allow_exception: bool = False,
    exception_reason: str | None = None,
    kto: str = "CTIP",
) -> DeviceIntakeResult:
    """Tworzy pojedyncze przyjecie PZ i rekord MASZYNA."""
    batch_result = create_device_intake_batch(
        items=[
            DeviceIntakeItemInput(
                model_id=int(model_id),
                serial=serial,
                ewidencja=ewidencja,
                purchase_price_netto=purchase_price_netto,
            )
        ],
        supplier_id=supplier_id,
        external_document=external_document,
        issued_by=issued_by,
        idempotency_key=idempotency_key,
        allow_exception=allow_exception,
        exception_reason=exception_reason,
        kto=kto,
    )
    return batch_result.items[0]


__all__ = [
    "DeviceCatalogSyncResult",
    "DeviceIntakeBatchResult",
    "DeviceIntakeItemInput",
    "DeviceIntakeResult",
    "create_device_model",
    "create_device_supplier",
    "get_next_ewidencja_suggestion",
    "load_device_model_taxonomy",
    "create_device_intake_batch",
    "create_device_intake",
    "search_device_models",
    "search_device_suppliers",
    "sync_device_catalog_from_models",
]
