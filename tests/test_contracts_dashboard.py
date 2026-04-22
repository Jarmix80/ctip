"""Testy pomocniczych funkcji dashboardu obslugi umow."""

from unittest.mock import patch

from app.core.config import settings
from app.services.contracts_dashboard import (
    load_available_devices_from_firebird_warehouse,
    normalize_device_key,
    normalize_nip,
)


def test_normalize_nip_removes_non_digits() -> None:
    assert normalize_nip("777-000-11-22") == "7770001122"
    assert normalize_nip("  ") == ""


def test_normalize_device_key_keeps_alnum_uppercase() -> None:
    assert normalize_device_key(" sn-001 / ab ") == "SN001AB"
    assert normalize_device_key(None) == ""


def test_load_available_devices_from_firebird_warehouse_keeps_pm_s_entries() -> None:
    class FakeCursor:
        def __init__(self) -> None:
            self.closed = False
            self.executed = False

        def execute(self, query: str, params: tuple[int, ...]) -> None:
            self.executed = True
            assert "FROM MAGAZYN" in query
            assert params == (settings.fb_warehouse_id,)

        def fetchall(self) -> list[tuple[object, ...]]:
            return [
                (
                    101,
                    "KP/5001",
                    "Ricoh IM C300",
                    "Ricoh",
                    "IM C300",
                    1,
                    None,
                    100,
                    123,
                    "23",
                    "NIE",
                ),
                (
                    102,
                    "KP/5002",
                    "Konica Minolta C250",
                    "Konica",
                    "C250",
                    2,
                    1,
                    200,
                    246,
                    "23",
                    "TAK",
                ),
                (103, "KP/5003", "Zuzyte", "Test", "Skip", 1, 1, 50, 61.5, "23", "NIE"),
            ]

        def close(self) -> None:
            self.closed = True

    class FakeConnection:
        def __init__(self) -> None:
            self.cursor_obj = FakeCursor()
            self.closed = False

        def cursor(self) -> FakeCursor:
            return self.cursor_obj

        def close(self) -> None:
            self.closed = True

    fake_connection = FakeConnection()

    with patch(
        "app.services.contracts_dashboard._firebird_connection", return_value=fake_connection
    ):
        devices = load_available_devices_from_firebird_warehouse()

    assert [device["row"] for device in devices] == ["101", "102"]
    assert devices[0]["ms_id_magazyn_table"] == "101"
    assert devices[0]["serial_required"] == "NIE"
    assert devices[0]["available_quantity"] == "1"
    assert devices[0]["reservation_status"] == "brak rezerwacji"
    assert devices[1]["serial_required"] == "TAK"
    assert devices[1]["available_quantity"] == "1"
    assert devices[1]["reservation_status"] == "czesciowa rezerwacja (1 z 2)"
    assert fake_connection.cursor_obj.closed is True
    assert fake_connection.closed is True
