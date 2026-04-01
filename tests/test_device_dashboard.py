"""Testy jednostkowe helperow modulu app.services.device_dashboard."""

from __future__ import annotations

from typing import Any

from app.services.device_dashboard import _find_serial_row


class _FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...] | None]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, sql: str, params: tuple[Any, ...]) -> None:
        self.calls.append((sql, params))

    def fetchone(self) -> tuple[Any, ...] | None:
        if not self.rows:
            return None
        return self.rows.pop(0)


def _sample_serial_row() -> tuple[Any, ...]:
    return (
        9101,
        7001,
        None,
        None,
        None,
        2801,
        2801,
        8101,
        "SN-ABC-001",
        "KP/0001",
        "",
        None,
        None,
    )


def test_find_serial_row_uzywa_id_serial_z_zakpozycji_jako_mapowania_1_do_1() -> None:
    cursor = _FakeCursor([_sample_serial_row()])

    row = _find_serial_row(
        cursor,
        pz_id=7001,
        purchase_id_serial=9101,
        purchase_serial="SN-ABC-001",
        purchase_ewidencja="KP/0001",
    )

    assert row is not None
    assert row["id_serial"] == 9101
    assert len(cursor.calls) == 1
    sql, params = cursor.calls[0]
    assert "WHERE ID_SERIAL = ?" in sql
    assert params == (9101,)


def test_find_serial_row_fallback_ogranicza_sie_do_tego_samego_pz() -> None:
    cursor = _FakeCursor([_sample_serial_row()])

    row = _find_serial_row(
        cursor,
        pz_id=7001,
        purchase_id_serial=None,
        purchase_serial="SN-ABC-001",
        purchase_ewidencja="KP/0001",
    )

    assert row is not None
    assert row["id_serial"] == 9101
    assert len(cursor.calls) == 1
    sql, params = cursor.calls[0]
    assert "WHERE ID_PZ = ?" in sql
    assert "COALESCE(SERIAL, '')" in sql
    assert "COALESCE(EWIDENCJA, '')" in sql
    assert params[0] == 7001


def test_find_serial_row_bez_id_i_bez_kluczy_nie_wykonuje_losowego_dopasowania() -> None:
    cursor = _FakeCursor([])

    row = _find_serial_row(
        cursor,
        pz_id=7001,
        purchase_id_serial=None,
        purchase_serial="",
        purchase_ewidencja="",
    )

    assert row is None
    assert cursor.calls == []
