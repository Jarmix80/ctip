"""Operacje zapisu Firebird dla procesu przyjecia urzadzen w module /device."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from app.core.config import settings

DEVICE_WAREHOUSE_ID = 28
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
DEFAULT_MACHINE_STOI = "MAGAZYN 28"
DEFAULT_MACHINE_SERVICE_KIND = "Platne"
DEFAULT_EWIDENCJA_PREFIX = "KP/"
DEFAULT_EWIDENCJA_WIDTH = 4
SUPPLIER_TYPE_VALUE = "Dostawca"
SUPPLIER_KIND_VALUE = 0
MODEL_ID_OUTLIER_THRESHOLD = 1_000_000


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


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_local_firebird_path() -> Path:
    db_path = Path(settings.fb_local_copy_path)
    if not db_path.is_absolute():
        db_path = _repo_root() / db_path
    return db_path


def _firebird_connection():
    import firebirdsql  # type: ignore[import-not-found]

    db_path = _resolve_local_firebird_path()
    if not db_path.exists():
        raise FileNotFoundError(f"Brak lokalnej kopii Firebird: {db_path}")
    return firebirdsql.connect(
        host="127.0.0.1",
        port=settings.fb_port,
        database=str(db_path),
        user=settings.fb_user,
        password=settings.fb_password,
        charset=settings.fb_charset,
    )


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
    max_value = 0
    width = DEFAULT_EWIDENCJA_WIDTH
    max_digits = 6

    # Dla glownego prefiksu KP/ wybieramy szerokosc numeracji dominujaca w danych
    # (najczesciej 4 cyfry) i ignorujemy testowe outliery.
    if prefix_value == "KP/":
        candidates: list[tuple[int, int]] = []
        for table_name, id_column in (("SERIAL", "ID_SERIAL"), ("MASZYNA", "ID_MASZYNA_TABLE")):
            cursor.execute(
                f"""
                SELECT FIRST 2000 EWIDENCJA
                FROM {table_name}
                ORDER BY {id_column} DESC
                """
            )
            for (raw_ewidencja,) in cursor.fetchall():
                parsed_value, parsed_width = _extract_trailing_number(
                    _text(raw_ewidencja, 100),
                    prefix_value,
                )
                if parsed_value is None:
                    continue
                if parsed_width > max_digits:
                    continue
                candidates.append((parsed_value, parsed_width))
        picked = _pick_next_from_width_mode(candidates)
        if picked is not None:
            return picked

    for table_name in ("SERIAL", "MASZYNA"):
        cursor.execute(
            f"""
            SELECT EWIDENCJA
            FROM {table_name}
            WHERE UPPER(COALESCE(EWIDENCJA, '')) STARTING WITH ?
            """,
            (prefix_value,),
        )
        for (raw_ewidencja,) in cursor.fetchall():
            parsed_value, parsed_width = _extract_trailing_number(
                _text(raw_ewidencja, 100), prefix_value
            )
            if parsed_value is None:
                continue
            if parsed_width > max_digits:
                continue
            if prefix_value == "KP/":
                # Dla fallbacku KP/ dalej trzymamy sie dominujacej szerokosci numeracji.
                continue
            max_value = max(max_value, parsed_value)
            width = max(width, parsed_width)
    if prefix_value == "KP/":
        kp_candidates: list[tuple[int, int]] = []
        for table_name in ("SERIAL", "MASZYNA"):
            cursor.execute(
                f"""
                SELECT EWIDENCJA
                FROM {table_name}
                WHERE UPPER(COALESCE(EWIDENCJA, '')) STARTING WITH ?
                """,
                (prefix_value,),
            )
            for (raw_ewidencja,) in cursor.fetchall():
                parsed_value, parsed_width = _extract_trailing_number(
                    _text(raw_ewidencja, 100), prefix_value
                )
                if parsed_value is None or parsed_width > max_digits:
                    continue
                kp_candidates.append((parsed_value, parsed_width))
        picked = _pick_next_from_width_mode(kp_candidates)
        if picked is not None:
            return picked
    return max_value + 1, width


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
            KOLOR
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


def search_device_models(*, query: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Zwraca liste modeli dla pola wyboru w formularzu przyjecia."""
    safe_limit = max(1, min(int(limit), 500))
    normalized_query = _text(query, 100)
    connection = _firebird_connection()
    cursor = connection.cursor()
    try:
        if normalized_query:
            text_query = normalized_query.upper()
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
                WHERE UPPER(COALESCE(m.MARKA, '')) CONTAINING ?
                   OR UPPER(COALESCE(m.MODEL, '')) CONTAINING ?
                   OR CAST(m.ID_MODEL AS VARCHAR(100)) CONTAINING ?
                ORDER BY m.MARKA, m.MODEL, m.ID_MODEL
                """,
                (
                    DEVICE_WAREHOUSE_ID,
                    AUTO_INDEX_PREFIX,
                    text_query,
                    text_query,
                    normalized_query,
                ),
            )
        else:
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
                ORDER BY m.MARKA, m.MODEL, m.ID_MODEL
                """,
                (DEVICE_WAREHOUSE_ID, AUTO_INDEX_PREFIX),
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
    supplier_filter = "(UPPER(TRIM(COALESCE(TYP, ''))) = 'DOSTAWCA' " "OR COALESCE(RODZAJ, 0) = 4)"
    connection = _firebird_connection()
    cursor = connection.cursor()
    try:
        if normalized_query:
            text_query = normalized_query.upper()
            nip_query = _normalize_nip(normalized_query)
            name_query = text_query[:500]
            address_query = text_query[:250]
            city_query = text_query[:150]
            nip_filter = nip_query[:20]
            sql = f"""
                SELECT FIRST {safe_limit}
                    ID_KLIENT,
                    NAZWA,
                    NIP,
                    ADRES,
                    KOD,
                    POCZTA,
                    TELEFON,
                    E_MAIL
                FROM KLIENT
                WHERE {supplier_filter}
                  AND (
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
                f"""
                SELECT FIRST {safe_limit}
                    ID_KLIENT,
                    NAZWA,
                    NIP,
                    ADRES,
                    KOD,
                    POCZTA,
                    TELEFON,
                    E_MAIL
                FROM KLIENT
                WHERE {supplier_filter}
                ORDER BY NAZWA, ID_KLIENT
                """
            )

        result: list[dict[str, Any]] = []
        for row in cursor.fetchall():
            if row[0] is None:
                continue
            result.append(
                {
                    "id_klient": int(row[0]),
                    "nazwa": _text(row[1], 500),
                    "nip": _text(row[2], 30),
                    "adres": _text(row[3], 250),
                    "kod": _text(row[4], 6),
                    "poczta": _text(row[5], 150),
                    "telefon": _text(row[6], 100),
                    "email": _text(row[7], 200),
                }
            )
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
                    if not _is_supplier_marker(existing):
                        cursor.execute(
                            """
                            UPDATE KLIENT
                            SET
                                TYP = ?,
                                RODZAJ = COALESCE(RODZAJ, ?)
                            WHERE ID_KLIENT = ?
                            """,
                            (
                                SUPPLIER_TYPE_VALUE,
                                SUPPLIER_KIND_VALUE,
                                int(row[0]),
                            ),
                        )
                        existing = _find_supplier(cursor, int(row[0])) or existing
                        connection.commit()
                    return existing

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
                _text(f"AUTO-DEVICE SUPPLIER {kto}", 1000),
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
    sync_catalog: bool = True,
    kto: str = "CTIP",
) -> dict[str, Any]:
    """Tworzy rekord MODEL i opcjonalnie kartoteke AUTO na magazynie 28."""
    marka_value = _text(marka, 50)
    model_value = _normalize_model_name_for_brand(marka_value, model_name)
    if not marka_value:
        raise ValueError("Pole marka jest wymagane.")
    if not model_value:
        raise ValueError("Pole model jest wymagane.")

    connection = _firebird_connection()
    cursor = connection.cursor()
    try:
        existing = _find_model_by_signature(
            cursor,
            marka=marka_value,
            model_name=model_value,
        )
        created = False
        if existing is not None:
            model_id = int(existing["id_model"])
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
                    _text(grupa, 50) or "Druk",
                    _text(rodzaj, 50) or "MFP",
                    "TAK" if kolor else "NIE",
                    _text(plik, 250),
                    _text(f"AUTO-DEVICE MODEL {kto}", 50),
                ),
            )
            created = True

        catalog_payload: dict[str, Any] | None = None
        if sync_catalog:
            next_index_number = _next_auto_index_number(cursor)
            action, warehouse_item_id, index_value, _ = _upsert_auto_catalog_item(
                cursor,
                model_id=model_id,
                marka=marka_value,
                model_name=model_value,
                only_missing=False,
                next_index_number=next_index_number,
                kto=kto,
            )
            catalog_payload = {
                "action": action,
                "warehouse_item_id": warehouse_item_id,
                "index": index_value,
            }

        connection.commit()
        return {
            "created": created,
            "id_model": model_id,
            "marka": marka_value,
            "model": model_value,
            "catalog": catalog_payload,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


def _next_pz_document_number(cursor) -> int:
    cursor.execute(
        """
        SELECT COALESCE(MAX(DOKUMENT), 0)
        FROM ZAKUPY
        WHERE RODZAJ_DOK = 'PZ'
        """
    )
    current = cursor.fetchone()
    return int((current[0] if current else 0) or 0) + 1


def _find_machine_conflict(cursor, serial_key: str, ewidencja_key: str) -> dict[str, Any] | None:
    cursor.execute(
        """
        SELECT FIRST 1
            ID_MASZYNA,
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
        "serial": _text(row[1], 100),
        "serial2": _text(row[2], 100),
        "ewidencja": _text(row[3], 100),
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


def _find_serial_by_pz(cursor, *, pz_id: int, serial_key: str, ewidencja_key: str) -> int | None:
    cursor.execute(
        """
        SELECT FIRST 1 ID_SERIAL
        FROM SERIAL
        WHERE ID_PZ = ?
          AND (
              UPPER(REPLACE(REPLACE(REPLACE(COALESCE(SERIAL, ''), '/', ''), '-', ''), ' ', '')) = ?
              OR UPPER(REPLACE(REPLACE(REPLACE(COALESCE(EWIDENCJA, ''), '/', ''), '-', ''), ' ', '')) = ?
          )
        ORDER BY ID_SERIAL DESC
        """,
        (pz_id, serial_key, ewidencja_key),
    )
    row = cursor.fetchone()
    if row is None or row[0] is None:
        return None
    return int(row[0])


def _insert_serial_row(
    cursor,
    *,
    supplier_id: int,
    warehouse_item_id: int,
    pz_id: int,
    serial_value: str,
    ewidencja_value: str,
) -> int:
    cursor.execute(
        """
        INSERT INTO SERIAL (
            ID_FIRMA,
            ID_DOSTAWCA,
            ID_MAGPOZ,
            ID_MAGAZYN,
            ID_PZ,
            SERIAL,
            EWIDENCJA,
            DATA_ZAKU,
            ZAKUP,
            UWAGI,
            STAN
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING ID_SERIAL
        """,
        (
            1,
            supplier_id,
            warehouse_item_id,
            DEVICE_WAREHOUSE_ID,
            pz_id,
            serial_value,
            ewidencja_value,
            date.today(),
            Decimal("0"),
            "AUTO-INTAKE",
            "PZ",
        ),
    )
    row = cursor.fetchone()
    if row is None or row[0] is None:
        raise RuntimeError("Nie udalo sie utworzyc rekordu SERIAL.")
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


def _assign_machine_to_serial(cursor, *, serial_id: int, machine_id: int) -> None:
    cursor.execute(
        """
        UPDATE SERIAL
        SET ID_MASZYNA = ?
        WHERE ID_SERIAL = ?
        """,
        (machine_id, serial_id),
    )


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


def create_device_intake_batch(
    *,
    items: list[DeviceIntakeItemInput],
    supplier_id: int | None = None,
    external_document: str | None = None,
    issued_by: str | None = None,
    force: bool = False,
    ewidencja_prefix: str | None = None,
    kto: str = "CTIP",
) -> DeviceIntakeBatchResult:
    """Tworzy jeden dokument PZ z wieloma egzemplarzami i zaklada rekord MASZYNA."""
    if not items:
        raise ValueError("Lista pozycji przyjecia jest pusta.")

    resolved_supplier_id = int(supplier_id or settings.fb_warehouse_client_id)
    connection = _firebird_connection()
    cursor = connection.cursor()
    try:
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

        today = date.today()
        issued_by_value = _text(issued_by, 100) or "CTIP"
        document_number = _next_pz_document_number(cursor)
        pz_number = _text(f"PZ / {document_number} / {today.year}", 30)
        external_number = _text(external_document, 30) or _text(
            f"CTIP-AUTO-{today.strftime('%Y%m%d')}-{document_number}",
            30,
        )

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
                0,
            ),
        )
        pz_id = int(cursor.fetchone()[0])

        next_index_number = _next_auto_index_number(cursor)
        created_items: list[DeviceIntakeResult] = []

        for item in prepared_items:
            model_details = _find_model_details(cursor, int(item["model_id"]))
            if model_details is None:
                raise ValueError(f"Nie znaleziono MODEL.ID_MODEL={item['model_id']}.")

            if not force:
                serial_conflict = _find_serial_conflict(
                    cursor,
                    str(item["serial_key"]),
                    str(item["ewidencja_key"]),
                )
                if serial_conflict is not None:
                    raise ValueError(
                        "Wykryto istniejacy wpis serial/ewidencja w tabeli SERIAL. "
                        f"ID_SERIAL={serial_conflict['id_serial']}, ID_PZ={serial_conflict['id_pz']}."
                    )
                machine_conflict = _find_machine_conflict(
                    cursor,
                    str(item["serial_key"]),
                    str(item["ewidencja_key"]),
                )
                if machine_conflict is not None:
                    raise ValueError(
                        "Wykryto istniejacy wpis serial/ewidencja w tabeli MASZYNA. "
                        f"ID_MASZYNA={machine_conflict['id_maszyna']}."
                    )

            _action, warehouse_item_id, warehouse_index, next_index_number = (
                _upsert_auto_catalog_item(
                    cursor,
                    model_id=int(model_details["id_model"]),
                    marka=str(model_details["marka"]),
                    model_name=str(model_details["model"]),
                    only_missing=False,
                    next_index_number=next_index_number,
                    kto=kto,
                )
            )

            catalog_item = _find_auto_catalog_item(cursor, int(model_details["id_model"]))
            if catalog_item is None:
                raise RuntimeError("Nie udalo sie odczytac kartoteki AUTO po zapisie.")
            vat_rate_text = _text(catalog_item.get("vat_stawka"), 20) or DEFAULT_VAT_RATE
            vat_percent = _parse_vat_rate(vat_rate_text)
            id_vat = int(catalog_item.get("idvat") or 1)

            price_net = _money(item["purchase_price_netto"])
            quantity = Decimal("1")
            value_net = _money(price_net * quantity)
            value_vat = _money(value_net * vat_percent / Decimal("100"))
            value_gross = _money(value_net + value_vat)

            display_name = (
                _text(
                    f"{model_details['marka']} {model_details['model']}",
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
                    EWIDENCJA
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    Decimal("0"),
                    Decimal("0"),
                    vat_rate_text,
                    value_vat,
                    id_vat,
                    value_gross,
                    Decimal("0"),
                    item["serial"],
                    item["ewidencja"],
                ),
            )
            zakpozycja_id = int(cursor.fetchone()[0])

            serial_id = _insert_serial_row(
                cursor,
                supplier_id=resolved_supplier_id,
                warehouse_item_id=warehouse_item_id,
                pz_id=pz_id,
                serial_value=str(item["serial"]),
                ewidencja_value=str(item["ewidencja"]),
            )
            cursor.execute(
                """
                UPDATE ZAKPOZYCJA
                SET ID_SERIAL = ?
                WHERE ID_ZAKPOZYCJA_TABLE = ?
                """,
                (serial_id, zakpozycja_id),
            )

            machine_id, machine_table_id = _insert_machine_row(
                cursor,
                model_details=model_details,
                serial_value=str(item["serial"]),
                ewidencja_value=str(item["ewidencja"]),
                issued_by_value=issued_by_value,
                pz_number=pz_number,
                kto=kto,
            )
            _assign_machine_to_serial(
                cursor,
                serial_id=serial_id,
                machine_id=machine_id,
            )

            created_items.append(
                DeviceIntakeResult(
                    model_id=int(model_details["id_model"]),
                    warehouse_item_id=warehouse_item_id,
                    warehouse_index=warehouse_index,
                    pz_id=pz_id,
                    pz_number=pz_number,
                    zakpozycja_id=zakpozycja_id,
                    serial_id=serial_id,
                    serial=str(item["serial"]),
                    ewidencja=str(item["ewidencja"]),
                    supplier_id=resolved_supplier_id,
                    machine_id=machine_id,
                    machine_table_id=machine_table_id,
                    purchase_price_netto=_money(item["purchase_price_netto"]),
                )
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
    supplier_id: int | None = None,
    external_document: str | None = None,
    issued_by: str | None = None,
    force: bool = False,
    kto: str = "CTIP",
) -> DeviceIntakeResult:
    """Tworzy pojedyncze przyjecie PZ i rekord MASZYNA."""
    batch_result = create_device_intake_batch(
        items=[
            DeviceIntakeItemInput(
                model_id=int(model_id),
                serial=serial,
                ewidencja=ewidencja,
            )
        ],
        supplier_id=supplier_id,
        external_document=external_document,
        issued_by=issued_by,
        force=force,
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
