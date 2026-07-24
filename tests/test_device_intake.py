"""Testy jednostkowe helperow modulu app.services.device_intake."""

from app.services.device_intake import (
    _ensure_intake_generators,
    _extract_trailing_number,
    _is_supplier_marker,
    _model_id_from_search_query,
    _normalize_model_name_for_brand,
    _pick_next_from_width_mode,
    _resolve_next_model_id_from_values,
    _supplier_id_from_search_query,
    search_device_suppliers,
)


def test_model_id_from_search_query_obsluguje_etykiete_datalist() -> None:
    assert _model_id_from_search_query("96 | Epson WF-C869R") == 96
    assert _model_id_from_search_query(" 96 ") == 96
    assert _model_id_from_search_query("Epson WF-C869R") is None
    assert _model_id_from_search_query("0 | błędny") is None


def test_supplier_id_from_search_query_wymaga_pelnej_etykiety() -> None:
    assert _supplier_id_from_search_query("648 | EUROIMPEX | NIP 8990107125") == 648
    assert _supplier_id_from_search_query("648") is None
    assert _supplier_id_from_search_query("EUROIMPEX") is None


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


def test_ensure_intake_generators_podnosi_tylko_opozniona_sekwencje() -> None:
    class GeneratorCursor:
        def __init__(self) -> None:
            self.last_sql = ""
            self.calls: list[str] = []

        def execute(self, sql: str) -> None:
            self.last_sql = sql
            self.calls.append(sql)

        def fetchone(self) -> tuple[int]:
            if "GEN_ID(LOG_ID_LOG_TABLE_GEN, 0)" in self.last_sql:
                return (10,)
            if "MAX(ID_LOG_TABLE)" in self.last_sql:
                return (13,)
            if "GEN_ID(LOG_ID_LOG_TABLE_GEN, 3)" in self.last_sql:
                return (13,)
            if "GEN_ID(" in self.last_sql:
                return (20,)
            return (19,)

    cursor = GeneratorCursor()

    _ensure_intake_generators(cursor)

    assert "SELECT GEN_ID(LOG_ID_LOG_TABLE_GEN, 3) FROM RDB$DATABASE" in cursor.calls
    assert not any("GEN_ID(MAGAZYN_ID_MAGAZYN_TABLE_GEN, 1)" in sql for sql in cursor.calls)


class _FakeCursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...] | None]] = []

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> None:
        self.calls.append((sql, params))

    def fetchall(self) -> list[tuple[object, ...]]:
        if "SELECT DISTINCT ID_KLIENT" in self.calls[-1][0]:
            return [(1571,)]
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
                "Odbiorca",
                0,
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
    assert rows[0]["used_on_pz"] is True
    assert "SELECT DISTINCT ID_KLIENT" in cursor.calls[0][0]
    sql, params = cursor.calls[1]
    assert "? <> ''" not in sql
    assert "UPPER(COALESCE(NIP, '')) CONTAINING ?" in sql
    assert params == ("1239", "1239", "1239", "1239")


def test_search_device_suppliers_bez_cyfr_pomija_warunek_nip(monkeypatch) -> None:
    cursor = _FakeCursor()
    connection = _FakeConnection(cursor)
    monkeypatch.setattr("app.services.device_intake._firebird_connection", lambda: connection)

    rows = search_device_suppliers(query="Andersia", limit=20)

    assert len(rows) == 1
    sql, params = cursor.calls[1]
    assert "UPPER(COALESCE(NIP, '')) CONTAINING ?" not in sql
    assert params == ("ANDERSIA", "ANDERSIA", "ANDERSIA")


def test_search_device_suppliers_rozpoznaje_etykiete_datalist(monkeypatch) -> None:
    cursor = _FakeCursor()
    connection = _FakeConnection(cursor)
    monkeypatch.setattr("app.services.device_intake._firebird_connection", lambda: connection)

    rows = search_device_suppliers(query="1571 | DOSTAWCA TEST | NIP 7781432866", limit=20)

    assert len(rows) == 1
    sql, params = cursor.calls[1]
    assert "WHERE ID_KLIENT = ?" in sql
    assert params == (1571,)
