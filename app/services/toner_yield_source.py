"""Odczyt kartotek i aktywnych umów MS w wymuszonej transakcji tylko do odczytu."""

import re

from app.services.telemetry.sources import firebird_connection


def model_key(brand: str, model: str) -> str:
    """Normalizuje zapis modelu, zachowując znaczący plus i rozdział rodzin MP/MP C."""
    normalized_brand = re.sub(r"[^A-Z0-9]", "", brand.upper())
    aliases = {
        "NASHUATEC": "RICOH",
        "GESTETNER": "RICOH",
        "LANIER": "RICOH",
        "KYOCERAMITA": "KYOCERA",
    }
    normalized_brand = aliases.get(normalized_brand, normalized_brand)
    normalized_model = re.sub(r"[^A-Z0-9+]", "", model.upper())
    for prefix in (normalized_brand, "RICOH", "NASHUATEC"):
        if normalized_model.startswith(prefix):
            normalized_model = normalized_model[len(prefix) :]
    return f"{normalized_brand}:{normalized_model}"


def toner_color(name: str) -> str:
    """Rozpoznaje wyłącznie jawne oznaczenia koloru; brak pozostaje nieznany."""
    patterns = {
        "black": r"\b(black|blk|bk|czarn\w*)\b|(?<=\d)BK\b",
        "cyan": r"\b(cyan|cyn|cyjan|cyjanowy|niebieski)\b",
        "magenta": r"\b(magenta|mag|mgt|czerwony|purpurowy)\b",
        "yellow": r"\b(yellow|yel|ylw|yllw|żółty|zolty)\b",
    }
    matches = [color for color, pattern in patterns.items() if re.search(pattern, name, re.I)]
    return matches[0] if len(matches) == 1 else "unknown"


def is_toner(name: str) -> bool:
    """Odrzuca pojemniki odpadowe, podzespoły i proszki zamiast pełnego wkładu."""
    return bool(re.search(r"\btoner\b", name, re.I)) and not re.search(
        r"waste|zużyt|zuzyt|pojemnik|butelka\s+na|proszek|developer|bęben|beben|chip|czujnik|silnik|sprzęg|sprzeg|ślimak|slimak|zasyp",
        name,
        re.I,
    )


def load_toner_catalog(warehouse_id: int) -> dict:
    """Pobiera jeden kompletny odczyt bez cen, klientów i wykonywania zapisu MS."""
    with firebird_connection() as connection:
        cursor = connection.cursor()
        cursor.execute(
            "SELECT ID_MAGAZYN_TABLE, INDEKS, NAZWA, MARKA, MODEL, NR_KAT1, NR_KAT2, ILOSC "
            "FROM MAGAZYN WHERE ID_MAGAZYN=? AND RODZAJ IN ('1. Część zamienna','2. Towar inny')",
            (warehouse_id,),
        )
        items = [
            {
                "item_id": int(row[0]),
                "item_index": (row[1] or "").strip(),
                "name": (row[2] or "").strip(),
                "brand": (row[3] or "").strip(),
                "stock": float(row[7] or 0),
            }
            for row in cursor.fetchall()
            if is_toner(row[2] or "")
        ]
        cursor.execute(
            "SELECT DISTINCT m.ID_MODEL, m.MARKA, m.MODEL, d.MARKA, d.MODEL "
            "FROM MASZYNA m JOIN UMOWACPC u ON u.ID_UMOWACPC_TABLE=m.ID_UMOWACPC "
            "LEFT JOIN MODEL d ON d.ID_MODEL=m.ID_MODEL "
            "WHERE m.AKTYWNA='TAK' AND u.AKTYWNA='TAK'"
        )
        active_keys = set()
        for row in cursor.fetchall():
            active_keys.add(model_key(row[1] or "", row[2] or ""))
            if row[3] and row[4]:
                active_keys.add(model_key(row[3], row[4]))
        cursor.execute("SELECT ID_MODEL, MARKA, MODEL FROM MODEL WHERE ID_MODEL IS NOT NULL")
        models = {
            int(row[0]): {
                "id": int(row[0]),
                "brand": (row[1] or "").strip(),
                "model": (row[2] or "").strip(),
            }
            for row in cursor.fetchall()
        }
    if not items:
        raise ValueError("Odczyt nie zawiera tonerów; poprzedni katalog pozostaje bez zmian.")
    return {
        "warehouse_id": warehouse_id,
        "items": items,
        "active_keys": sorted(active_keys),
        "models": models,
    }
