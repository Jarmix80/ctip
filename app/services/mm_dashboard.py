"""Logika raportu MM (przesuniecia miedzymagazynowe) dla widoku /mm."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from app.services.contracts_dashboard import _firebird_connection

DEST_ALL = "all"
DEST_ZLOM = "zlom"
DEST_WYNAJEM = "wynajem"
DEST_OPTIONS = {DEST_ALL, DEST_ZLOM, DEST_WYNAJEM}
_POLISH_CHARS_MAP = str.maketrans("ąćęłńóśżź", "acelnoszz")


def extract_model_label(item_name: str | None, index_value: str | None = None) -> str:
    """Buduje etykiete modelu na podstawie nazwy pozycji MM."""
    raw = str(item_name or "").strip()
    if not raw:
        return str(index_value or "").strip()

    # Usuwamy koncowki techniczne (S/N i nr.wew), aby zostal czytelny model.
    cleaned = re.split(r"(?i)\bS\s*/?\s*N\s*:", raw, maxsplit=1)[0]
    cleaned = re.split(r"(?i)\bnr\.?\s*wew", cleaned, maxsplit=1)[0]
    cleaned = cleaned.strip(" ,;-")
    return cleaned or raw


def _decimal_to_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _normalize_match_token(value: str | None) -> str:
    return str(value or "").strip().lower().translate(_POLISH_CHARS_MAP)


def _resolve_target_warehouses(
    warehouses: dict[int, str],
) -> tuple[list[int], list[int], dict[int, str]]:
    zlom_ids = [
        warehouse_id
        for warehouse_id, name in warehouses.items()
        if "zlom" in name.lower() or "złom" in name.lower()
    ]
    wynajem_ids = [
        warehouse_id for warehouse_id, name in warehouses.items() if "wynajem" in name.lower()
    ]
    kinds: dict[int, str] = {}
    for warehouse_id in zlom_ids:
        kinds[warehouse_id] = DEST_ZLOM
    for warehouse_id in wynajem_ids:
        kinds[warehouse_id] = DEST_WYNAJEM
    return zlom_ids, wynajem_ids, kinds


def _resolve_source_warehouses(warehouses: dict[int, str]) -> list[int]:
    source_ids: list[int] = []
    for warehouse_id, warehouse_name in warehouses.items():
        token = _normalize_match_token(warehouse_name)
        if "urzadzenia" not in token:
            continue
        if "magazyn" in token or "wynajem" in token:
            source_ids.append(warehouse_id)
    return sorted(set(source_ids))


def load_mm_dashboard_data(
    *,
    date_from: date,
    date_to: date,
    destination_filter: str = DEST_ALL,
    model_filter: str | None = None,
    search_filter: str | None = None,
    limit: int = 10000,
) -> dict[str, Any]:
    """Pobiera pozycje MM dla magazynow docelowych: zlom i wynajem."""
    if date_to < date_from:
        raise ValueError("Nieprawidlowy zakres dat: date_to < date_from.")
    if destination_filter not in DEST_OPTIONS:
        raise ValueError("Nieprawidlowa wartosc destination_filter.")

    safe_limit = max(100, min(int(limit), 50000))
    model_token = str(model_filter or "").strip().casefold()
    search_token = str(search_filter or "").strip().casefold()

    connection = _firebird_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT ID_MAGAZYN, TRIM(NAZWA)
            FROM MAGAZYNY
            ORDER BY ID_MAGAZYN
            """
        )
        warehouse_rows = cursor.fetchall()
        warehouses = {
            int(row[0]): str(row[1] or "").strip()
            for row in warehouse_rows
            if row and row[0] is not None
        }
        zlom_ids, wynajem_ids, warehouse_kinds = _resolve_target_warehouses(warehouses)
        source_ids = _resolve_source_warehouses(warehouses)

        if destination_filter == DEST_ZLOM:
            target_ids = zlom_ids
        elif destination_filter == DEST_WYNAJEM:
            target_ids = wynajem_ids
        else:
            target_ids = sorted(set(zlom_ids + wynajem_ids))

        if not target_ids:
            return {
                "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(),
                "destination_filter": destination_filter,
                "model_filter": model_filter or "",
                "search_filter": search_filter or "",
                "summary": {
                    "documents_count": 0,
                    "items_count": 0,
                    "quantity_sum": 0.0,
                    "truncated": False,
                },
                "warehouses": {
                    "zlom_ids": zlom_ids,
                    "wynajem_ids": wynajem_ids,
                    "target_ids": target_ids,
                    "source_ids": source_ids,
                    "names": {str(k): v for k, v in warehouses.items()},
                },
                "items": [],
            }

        if not source_ids:
            return {
                "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(),
                "destination_filter": destination_filter,
                "model_filter": model_filter or "",
                "search_filter": search_filter or "",
                "summary": {
                    "documents_count": 0,
                    "items_count": 0,
                    "quantity_sum": 0.0,
                    "truncated": False,
                },
                "warehouses": {
                    "zlom_ids": zlom_ids,
                    "wynajem_ids": wynajem_ids,
                    "target_ids": target_ids,
                    "source_ids": source_ids,
                    "names": {str(k): v for k, v in warehouses.items()},
                },
                "items": [],
            }

        target_ids_sql = ",".join(str(warehouse_id) for warehouse_id in target_ids)
        source_ids_sql = ",".join(str(warehouse_id) for warehouse_id in source_ids)
        cursor.execute(
            f"""
            SELECT
                z.ID_ZAKUPY_TABLE,
                TRIM(COALESCE(z.NUMER, '')),
                z.DATA_WYST,
                z.ID_MP,
                z.ID_MW,
                TRIM(COALESCE(z.UWAGI, '')),
                p.ID_ZAKPOZYCJA_TABLE,
                TRIM(COALESCE(p.INDEKS, '')),
                TRIM(COALESCE(p.NAZWA, '')),
                p.ILOSC,
                TRIM(COALESCE(p.JM, '')),
                TRIM(COALESCE(p.SERIAL, '')),
                TRIM(COALESCE(p.EWIDENCJA, '')),
                p.CENA_Z,
                p.CENA_NETTO
            FROM ZAKUPY z
            JOIN ZAKPOZYCJA p ON p.ID_ZAKUPY = z.ID_ZAKUPY_TABLE
            WHERE TRIM(z.RODZAJ_DOK) = 'MM'
              AND z.DATA_WYST BETWEEN ? AND ?
              AND z.ID_MP IN ({target_ids_sql})
              AND z.ID_MW IN ({source_ids_sql})
            ORDER BY z.DATA_WYST DESC, z.ID_ZAKUPY_TABLE DESC, p.ID_ZAKPOZYCJA_TABLE DESC
            """,
            (date_from, date_to),
        )
        rows = cursor.fetchall()
    finally:
        cursor.close()
        connection.close()

    items: list[dict[str, Any]] = []
    source_ids_set = set(source_ids)
    for row in rows:
        destination_id = int(row[3]) if row[3] is not None else None
        source_id = int(row[4]) if row[4] is not None else None
        if source_id is None or source_id not in source_ids_set:
            continue
        item_name = str(row[8] or "").strip()
        index_value = str(row[7] or "").strip()
        model_label = extract_model_label(item_name, index_value)
        document_number = str(row[1] or "").strip()
        serial_value = str(row[11] or "").strip()
        ewidencja_value = str(row[12] or "").strip()
        purchase_price_netto = _decimal_to_float(row[13] if row[13] is not None else row[14])

        if model_token and model_token not in model_label.casefold():
            continue
        if search_token:
            haystack = " | ".join(
                (
                    document_number,
                    index_value,
                    item_name,
                    serial_value,
                    ewidencja_value,
                    model_label,
                )
            ).casefold()
            if search_token not in haystack:
                continue

        items.append(
            {
                "id_zakupy_table": int(row[0]) if row[0] is not None else None,
                "numer_mm": document_number,
                "data_wyst": row[2].isoformat() if row[2] else None,
                "id_mp": destination_id,
                "magazyn_przyjmujacy": (
                    warehouses.get(destination_id, f"ID:{destination_id}")
                    if destination_id is not None
                    else ""
                ),
                "destination_kind": warehouse_kinds.get(destination_id, "other"),
                "id_mw": source_id,
                "magazyn_wydajacy": (
                    warehouses.get(source_id, f"ID:{source_id}") if source_id is not None else ""
                ),
                "uwagi_dokumentu": str(row[5] or "").strip(),
                "id_zakpozycja_table": int(row[6]) if row[6] is not None else None,
                "indeks": index_value,
                "nazwa_pozycji": item_name,
                "model_label": model_label,
                "ilosc": _decimal_to_float(row[9]),
                "jm": str(row[10] or "").strip(),
                "cena_zakupu_netto": purchase_price_netto,
                "serial": serial_value,
                "ewidencja": ewidencja_value,
            }
        )

    truncated = False
    if len(items) > safe_limit:
        items = items[:safe_limit]
        truncated = True

    unique_documents = {
        int(item["id_zakupy_table"]) for item in items if item.get("id_zakupy_table") is not None
    }
    quantity_sum = round(sum(float(item.get("ilosc") or 0.0) for item in items), 4)

    documents_by_destination: dict[str, int] = defaultdict(int)
    items_by_destination: dict[str, int] = defaultdict(int)
    quantity_by_destination: dict[str, float] = defaultdict(float)
    seen_docs_by_dest: set[tuple[str, int]] = set()
    for item in items:
        destination_name = str(item.get("magazyn_przyjmujacy") or "")
        destination_kind = str(item.get("destination_kind") or "")
        destination_key = (
            f"{destination_name} ({destination_kind})" if destination_name else destination_kind
        )
        items_by_destination[destination_key] += 1
        quantity_by_destination[destination_key] += float(item.get("ilosc") or 0.0)
        doc_id = item.get("id_zakupy_table")
        if isinstance(doc_id, int):
            key = (destination_key, doc_id)
            if key not in seen_docs_by_dest:
                seen_docs_by_dest.add(key)
                documents_by_destination[destination_key] += 1

    return {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "destination_filter": destination_filter,
        "model_filter": model_filter or "",
        "search_filter": search_filter or "",
        "summary": {
            "documents_count": len(unique_documents),
            "items_count": len(items),
            "quantity_sum": quantity_sum,
            "truncated": truncated,
            "documents_by_destination": dict(sorted(documents_by_destination.items())),
            "items_by_destination": dict(sorted(items_by_destination.items())),
            "quantity_by_destination": {
                key: round(value, 4) for key, value in sorted(quantity_by_destination.items())
            },
        },
        "warehouses": {
            "zlom_ids": zlom_ids,
            "wynajem_ids": wynajem_ids,
            "target_ids": target_ids,
            "source_ids": source_ids,
            "names": {str(k): v for k, v in warehouses.items()},
        },
        "items": items,
    }


__all__ = [
    "DEST_ALL",
    "DEST_OPTIONS",
    "DEST_WYNAJEM",
    "DEST_ZLOM",
    "extract_model_label",
    "load_mm_dashboard_data",
]
