"""Testy pomocniczych funkcji dashboardu obslugi umow."""

from unittest.mock import patch

from app.core.config import settings
from app.services.contracts_dashboard import (
    extract_stock_device_identity,
    find_model_in_firebird,
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
                    None,
                    "KP/5001",
                    "Ricoh IMC 300 S/N: C74P370058",
                    "",
                    "",
                    1,
                    None,
                    100,
                    123,
                    "23",
                    "NIE",
                ),
                (
                    102,
                    None,
                    "KP/5002",
                    "Ricoh MPC 2503 S/N: EEA1234567, nr.wew: WEKP/5002",
                    "",
                    "",
                    2,
                    1,
                    200,
                    246,
                    "23",
                    "",
                ),
                (103, None, "KP/5003", "Zuzyte", "Test", "Skip", 1, 1, 50, 61.5, "23", "NIE"),
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
    assert devices[0]["producer"] == "Ricoh"
    assert devices[0]["model"] == "IM C300"
    assert devices[0]["serial"] == "C74P370058"
    assert devices[0]["serial_required"] == "TAK"
    assert devices[0]["available_quantity"] == "1"
    assert devices[0]["reservation_status"] == "brak rezerwacji"
    assert devices[1]["model"] == "MP C2503"
    assert devices[1]["serial"] == "EEA1234567"
    assert devices[1]["ewidencja"] == "WEKP/5002"
    assert devices[1]["serial_required"] == "TAK"
    assert devices[1]["available_quantity"] == "1"
    assert devices[1]["reservation_status"] == "czesciowa rezerwacja (1 z 2)"
    assert fake_connection.cursor_obj.closed is True
    assert fake_connection.closed is True


def test_extract_stock_device_identity_reads_serial_and_internal_number() -> None:
    parsed = extract_stock_device_identity(
        "Ricoh MPC 2011 S/N: G479M130731, nr.wew: WEKP/2680",
        index_value="KP/9999",
    )

    assert parsed["producer"] == "Ricoh"
    assert parsed["model"] == "MP C2011"
    assert parsed["serial"] == "G479M130731"
    assert parsed["ewidencja"] == "WEKP/2680"


def test_find_model_in_firebird_normalizes_ricoh_model_variants_from_stock_name() -> None:
    class FakeCursor:
        def __init__(self) -> None:
            self.closed = False
            self.calls: list[tuple[str, tuple[str, ...]]] = []

        def execute(self, query: str, params: tuple[str, ...]) -> None:
            self.calls.append((query, params))

        def fetchone(self) -> tuple[object, ...] | None:
            if not self.calls:
                return None
            params = self.calls[-1][1]
            candidate = str(params[0]).strip().upper()
            if candidate in {"IM C5500", "RICOH IM C5500"}:
                return (542, "Ricoh", "IM C5500", "Druk", "MFP A3", "TAK", "ricoh_imc5500.png")
            return None

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
        match = find_model_in_firebird("Ricoh IMC 5500 S/N: 3139M420306")

    assert match.found is True
    assert match.id_model == 542
    assert match.marka == "Ricoh"
    assert match.model == "IM C5500"
    assert match.grupa == "Druk"
    assert match.rodzaj == "MFP A3"
    assert match.kolor == "TAK"
    assert match.plik == "ricoh_imc5500.png"
    assert fake_connection.cursor_obj.closed is True
    assert fake_connection.closed is True
    queried_candidates = {params[0] for _query, params in fake_connection.cursor_obj.calls}
    assert "IM C5500" in queried_candidates
