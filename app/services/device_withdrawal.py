"""Kontrolowane wycofanie dokumentu PZ utworzonego przez moduł urządzeń."""

from __future__ import annotations

from typing import Any

from app.services.firebird_runtime import firebird_connection

_MACHINE_REFERENCE_TABLES = (
    "CENNIK",
    "CPC",
    "CYKL",
    "FAKTURA",
    "FPOZYCJA",
    "KOSZTORYS",
    "KPOZYCJA",
    "LOCKT",
    "MAIL",
    "MZ",
    "NOTES",
    "PLIKI",
    "RECYKLING",
    "SERIAL",
    "SMS",
    "UMOWA",
    "WPOZYCJA",
    "ZADANIE",
    "ZLECENIE",
    "ZPOZYCJA",
)


def _placeholders(values: list[int]) -> str:
    return ",".join("?" for _ in values)


def _dependency_counts(cursor, machine_ids: list[int]) -> dict[str, int]:
    if not machine_ids:
        return {}
    placeholders = _placeholders(machine_ids)
    counts: dict[str, int] = {}
    for table in _MACHINE_REFERENCE_TABLES:
        cursor.execute(
            f"SELECT COUNT(*) FROM {table} WHERE ID_MASZYNA IN ({placeholders})",
            tuple(machine_ids),
        )
        count = int(cursor.fetchone()[0] or 0)
        if count:
            counts[table] = count
    return counts


def _preview(cursor, *, pz_id: int, expected: dict[str, Any]) -> dict[str, Any]:
    cursor.execute(
        "SELECT NUMER, DOK_ZEW FROM ZAKUPY WHERE ID_ZAKUPY_TABLE = ? AND RODZAJ_DOK = 'PZ'",
        (int(pz_id),),
    )
    document = cursor.fetchone()
    expected_items = list(expected.get("items") or [])
    expected_position_ids = sorted(
        int(item["zakpozycja_id"])
        for item in expected_items
        if item.get("zakpozycja_id") is not None
    )
    cursor.execute(
        "SELECT ID_ZAKPOZYCJA_TABLE FROM ZAKPOZYCJA WHERE ID_ZAKUPY = ? ORDER BY 1",
        (int(pz_id),),
    )
    current_position_ids = [int(row[0]) for row in cursor.fetchall()]
    machine_ids = [
        int(item["machine_id"]) for item in expected_items if item.get("machine_id") is not None
    ]
    dependencies = _dependency_counts(cursor, machine_ids)
    differences: list[str] = []
    if document is None:
        differences.append("Dokument PZ nie istnieje w Firebird.")
    if current_position_ids != expected_position_ids:
        differences.append("Lista pozycji dokumentu PZ zmieniła się od chwili utworzenia.")
    baseline_complete = bool(expected_items) and len(expected_position_ids) == len(expected_items)
    if not baseline_complete:
        differences.append("Brak pełnego zapisu początkowego pozycji PZ w CTIP.")
    return {
        "exists": document is not None,
        "pz_id": int(pz_id),
        "pz_number": str(expected.get("pz_number") or ""),
        "positions": len(current_position_ids),
        "expected_positions": len(expected_items),
        "differences": differences,
        "dependencies": dependencies,
        "baseline_complete": baseline_complete,
        "can_withdraw_normally": not differences and not dependencies,
    }


def preview_device_pz_withdrawal(*, pz_id: int, expected: dict[str, Any]) -> dict[str, Any]:
    """Zwraca aktualne skutki i blokady wycofania bez modyfikacji Firebird."""
    connection = firebird_connection()
    cursor = connection.cursor()
    try:
        result = _preview(cursor, pz_id=pz_id, expected=expected)
        connection.rollback()
        return result
    finally:
        cursor.close()
        connection.close()


def withdraw_device_pz(
    *,
    pz_id: int,
    expected: dict[str, Any],
    force: bool,
) -> dict[str, Any]:
    """Ponownie sprawdza stan i atomowo usuwa elementy utworzone przez PZ."""
    connection = firebird_connection()
    cursor = connection.cursor()
    try:
        preview = _preview(cursor, pz_id=pz_id, expected=expected)
        if not preview["exists"]:
            connection.rollback()
            return {**preview, "already_withdrawn": True}
        if not force and not preview["can_withdraw_normally"]:
            raise ValueError(
                "Stan PZ zmienił się lub ma późniejsze powiązania. "
                "Skontaktuj się z administratorem."
            )
        if force and not preview["baseline_complete"]:
            raise ValueError("Nie można wymusić operacji bez pełnego zapisu początkowego CTIP.")

        items = list(expected.get("items") or [])
        machine_ids = [
            int(item["machine_id"]) for item in items if item.get("machine_id") is not None
        ]
        machine_table_ids = [
            int(item["machine_table_id"])
            for item in items
            if item.get("machine_table_id") is not None
        ]
        warehouse_ids = [
            int(item["warehouse_item_id"])
            for item in items
            if item.get("warehouse_item_id") is not None
        ]
        position_ids = [
            int(item["zakpozycja_id"]) for item in items if item.get("zakpozycja_id") is not None
        ]
        if force and machine_ids:
            placeholders = _placeholders(machine_ids)
            for table in _MACHINE_REFERENCE_TABLES:
                cursor.execute(
                    f"UPDATE {table} SET ID_MASZYNA = 0 " f"WHERE ID_MASZYNA IN ({placeholders})",
                    tuple(machine_ids),
                )
        if position_ids:
            cursor.execute(
                f"DELETE FROM ZAKPOZYCJA WHERE ID_ZAKPOZYCJA_TABLE IN "
                f"({_placeholders(position_ids)})",
                tuple(position_ids),
            )
        if machine_table_ids:
            cursor.execute(
                f"DELETE FROM MASZYNA WHERE ID_MASZYNA_TABLE IN "
                f"({_placeholders(machine_table_ids)})",
                tuple(machine_table_ids),
            )
        if warehouse_ids:
            cursor.execute(
                f"DELETE FROM MAGAZYN WHERE ID_MAGAZYN_TABLE IN "
                f"({_placeholders(warehouse_ids)})",
                tuple(warehouse_ids),
            )
        cursor.execute("DELETE FROM ZAKUPY WHERE ID_ZAKUPY_TABLE = ?", (int(pz_id),))
        connection.commit()
        return {**preview, "already_withdrawn": False}
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


__all__ = ["preview_device_pz_withdrawal", "withdraw_device_pz"]
