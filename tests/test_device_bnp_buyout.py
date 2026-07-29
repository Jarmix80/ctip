"""Testy jednostkowe procesu wykupu urządzeń BNP."""

from decimal import Decimal

import pytest

from app.services.device_bnp_buyout import (
    _build_buyout_identifiers,
    _build_lookup_payload,
    _normalize_serial_key,
    _validate_target_identifier,
)


def test_build_buyout_identifiers_zachowuje_dopiski_ewidencji() -> None:
    target_ewidencja, warehouse_index = _build_buyout_identifiers("KP/4579/SRS/SmartIT")

    assert target_ewidencja == "WKP/4579/SRS/SmartIT"
    assert warehouse_index == "WKP/4579/BNP"


def test_validate_target_identifier_blokuje_zmiane_numeru_kp() -> None:
    with pytest.raises(ValueError, match="musi zachować numer KP/4579"):
        _validate_target_identifier(
            "WKP/4580/BNP",
            expected_number="4579",
            field_name="MAGAZYN.INDEKS",
        )


def test_normalize_serial_key_usuwa_separatory() -> None:
    assert _normalize_serial_key(" 31-01/RC 80528 ") == "3101RC80528"


class _LookupCursor:
    def __init__(
        self,
        *,
        warehouse_rows: list[tuple[object, ...]] | None = None,
    ) -> None:
        self.result: list[tuple[object, ...]] = []
        self.warehouse_rows = warehouse_rows or []

    def execute(self, sql: str, _params=None) -> None:
        if "FROM MASZYNA m" in sql:
            self.result = [
                (
                    5189,
                    7112,
                    1790,
                    "KLIENT TESTOWY",
                    "7770000000",
                    97,
                    "Ricoh",
                    "IM C3000",
                    "3101RC80528",
                    "",
                    "KP/4579/SRS/SmartIT",
                    "TAK",
                    "Poznań",
                    "Testowa 1",
                    "Poznań",
                )
            ]
            return
        if "FROM MAGAZYN m" in sql:
            self.result = self.warehouse_rows
            return
        if "FROM KLIENT" in sql:
            self.result = [
                (
                    1937,
                    "BNP PARIBAS LEASE GROUP",
                    "Grzybowska 78",
                    "00-844",
                    "Warszawa",
                    "1132061128",
                )
            ]
            return
        raise AssertionError(f"Nieobsługiwane zapytanie testowe: {sql}")

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.result


def test_lookup_pozwala_utworzyc_brakujaca_kartoteke_magazynu_27() -> None:
    lookup = _build_lookup_payload(_LookupCursor(), "3101RC80528")

    assert lookup["machine"]["client_name"] == "KLIENT TESTOWY"
    assert lookup["suggested_ewidencja"] == "WKP/4579/SRS/SmartIT"
    assert lookup["suggested_index"] == "WKP/4579/BNP"
    assert lookup["can_create_catalog"] is True
    assert lookup["can_complete"] is False
    assert lookup["blockers"] == []


def test_lookup_blokuje_finalizacje_dla_dodatniego_stanu_magazynu_27() -> None:
    cursor = _LookupCursor(
        warehouse_rows=[
            (
                18592,
                27,
                "Urządzenia Wynajem",
                "WKP/4579/BNP",
                "Ricoh IM C3000 S/N: 3101RC80528",
                Decimal("1"),
                Decimal("23.35"),
                Decimal("0"),
                97,
                "Ricoh",
                "IM C3000",
                "NIE",
            )
        ]
    )

    lookup = _build_lookup_payload(cursor, "3101RC80528")

    assert lookup["target_item"]["id_magazyn_table"] == 18592
    assert lookup["can_create_catalog"] is False
    assert lookup["can_complete"] is False
    assert any("stan różny od 0" in blocker for blocker in lookup["blockers"])
