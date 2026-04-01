"""Testy jednostkowe helperow modulu app.services.device_intake."""

from app.services.device_intake import (
    _extract_trailing_number,
    _is_supplier_marker,
    _normalize_model_name_for_brand,
    _pick_next_from_width_mode,
    _resolve_next_model_id_from_values,
    search_device_suppliers,
)


def test_extract_trailing_number_wspiera_tylko_czysty_suffix_cyfrowy() -> None:
    value, width = _extract_trailing_number("KP/0012", "KP/")
    assert value == 12
    assert width == 4

    value, width = _extract_trailing_number("KP/0012A", "KP/")
    assert value is None
    assert width == 0


def test_normalize_model_name_for_brand_ricoh_formatuje_imc() -> None:
    assert _normalize_model_name_for_brand("Ricoh", "imc345") == "IM C345"
    assert _normalize_model_name_for_brand("Canon", "imc345") == "imc345"


def test_is_supplier_marker_sprawdza_typ_lub_rodzaj() -> None:
    assert _is_supplier_marker({"typ": "Dostawca", "rodzaj": 0}) is True
    assert _is_supplier_marker({"typ": "Odbiorca", "rodzaj": 4}) is True
    assert _is_supplier_marker({"typ": "Odbiorca", "rodzaj": 0}) is False


def test_pick_next_from_width_mode_ignoruje_outliery_szerokosci() -> None:
    next_value = _pick_next_from_width_mode(
        [
            (5071, 4),
            (5072, 4),
            (5073, 4),
            (999999, 6),
            (88888897, 8),
        ]
    )
    assert next_value == (5074, 4)


def test_resolve_next_model_id_from_values_ignoruje_outliery_duzych_id() -> None:
    assert _resolve_next_model_id_from_values([33, 631, 30002486, 30002523]) == 632
    assert _resolve_next_model_id_from_values([30002486, 30002523]) == 30002524
    assert _resolve_next_model_id_from_values([]) == 1


class _FakeCursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...] | None]] = []

    def execute(self, sql: str, params: tuple[str, ...] | None = None) -> None:
        self.calls.append((sql, params))

    def fetchall(self) -> list[tuple[object, ...]]:
        return [
            (
                1571,
                "DOSTAWCA TEST",
                "7781432866",
                "Testowa 1",
                "60-001",
                "Poznan",
                "",
                "",
            )
        ]

    def close(self) -> None:
        return None


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def close(self) -> None:
        return None


def test_search_device_suppliers_nie_uzywa_porownania_parametru_do_pustego_ciagu(
    monkeypatch,
) -> None:
    cursor = _FakeCursor()
    connection = _FakeConnection(cursor)
    monkeypatch.setattr("app.services.device_intake._firebird_connection", lambda: connection)

    rows = search_device_suppliers(query="1239", limit=20)

    assert len(rows) == 1
    sql, params = cursor.calls[0]
    assert "? <> ''" not in sql
    assert "UPPER(COALESCE(NIP, '')) CONTAINING ?" in sql
    assert params == ("1239", "1239", "1239", "1239")


def test_search_device_suppliers_bez_cyfr_pomija_warunek_nip(monkeypatch) -> None:
    cursor = _FakeCursor()
    connection = _FakeConnection(cursor)
    monkeypatch.setattr("app.services.device_intake._firebird_connection", lambda: connection)

    rows = search_device_suppliers(query="Andersia", limit=20)

    assert len(rows) == 1
    sql, params = cursor.calls[0]
    assert "UPPER(COALESCE(NIP, '')) CONTAINING ?" not in sql
    assert params == ("ANDERSIA", "ANDERSIA", "ANDERSIA")
