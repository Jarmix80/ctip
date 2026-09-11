"""Testy jednostkowe procesu wykupu urządzeń BNP."""

from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest

from app.services import device_bnp_buyout as buyout_service
from app.services.device_bnp_buyout import (
    _build_buyout_identifiers,
    _build_lookup_payload,
    _normalize_serial_key,
    _validate_target_identifier,
    complete_bnp_buyout,
    create_bnp_catalog_item,
)


def test_build_buyout_identifiers_zachowuje_dopiski_ewidencji() -> None:
    identifiers = _build_buyout_identifiers("KP/4579/SRS/SmartIT")

    assert identifiers.mode == "kp"
    assert identifiers.value == "4579"
    assert identifiers.ewidencja == "WKP/4579/SRS/SmartIT"
    assert identifiers.warehouse_index == "WKP/4579/BNP"


def test_validate_target_identifier_blokuje_zmiane_numeru_kp() -> None:
    with pytest.raises(ValueError, match="musi zachować numer KP/4579"):
        _validate_target_identifier(
            "WKP/4580/BNP",
            expected_value="4579",
            identifier_mode="kp",
            field_name="MAGAZYN.INDEKS",
        )


def test_normalize_serial_key_usuwa_separatory() -> None:
    assert _normalize_serial_key(" 31-01/RC 80528 ") == "3101RC80528"


class _LookupCursor:
    def __init__(
        self,
        *,
        warehouse_rows: list[tuple[object, ...]] | None = None,
        ewidencja: str = "KP/4579/SRS/SmartIT",
        serial: str = "3101RC80528",
        serial2: str = "",
    ) -> None:
        self.result: list[tuple[object, ...]] = []
        self.warehouse_rows = warehouse_rows or []
        self.machine_rows = [
            (
                5189,
                7112,
                1790,
                "KLIENT TESTOWY",
                "7770000000",
                97,
                "HSM",
                "SECURIO",
                serial,
                serial2,
                ewidencja,
                "TAK",
                "Poznań",
                "Testowa 1",
                "Poznań",
            )
        ]
        self.supplier_rows = [
            (1937, "BNP PARIBAS LEASE GROUP", "Grzybowska 78", "00-844", "Warszawa", "1132061128")
        ]

    def execute(self, sql: str, _params=None) -> None:
        if "FROM MASZYNA m" in sql:
            self.result = self.machine_rows
            return
        if "FROM MAGAZYN m" in sql:
            base_index, prefix = _params
            self.result = [
                row
                for row in self.warehouse_rows
                if str(row[3]).upper() == base_index or str(row[3]).upper().startswith(prefix)
            ]
            return
        if "FROM KLIENT" in sql:
            self.result = self.supplier_rows
            return
        raise AssertionError(f"Nieobsługiwane zapytanie testowe: {sql}")

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.result

    def fetchone(self):
        return self.result[0] if self.result else None

    def close(self) -> None:
        pass


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


@pytest.mark.parametrize("source", ["", "B630205697/BNP", "STARA EWIDENCJA", "KP/ABC"])
def test_lookup_dopuszcza_nietypowa_ewidencje_z_ostrzezeniem(source: str) -> None:
    """Brak poprawnego KP daje identyfikację serialem niezależnie od starego oznaczenia."""
    cursor = _LookupCursor(ewidencja=source, serial="B630205697")
    lookup = _build_lookup_payload(cursor, "B630205697")

    assert lookup["identifier_mode"] == "serial"
    assert lookup["identifier_value"] == "B630205697"
    assert lookup["suggested_ewidencja"] == lookup["suggested_index"] == "WKP/B630205697"
    assert lookup["can_create_catalog"] is True
    assert lookup["blockers"] == []
    assert "MASZYNA.EWIDENCJA nie ma formatu KP/<numer>/..." in lookup["warnings"][0]
    assert "numeru seryjnego B630205697" in lookup["warnings"][0]


def _warehouse_row(
    index: str, quantity: str = "0", *, item_id: int = 18592, warehouse_id: int = 27
) -> tuple[object, ...]:
    """Zwraca kartotekę magazynową o zadanej tożsamości i stanie."""
    return (
        item_id,
        warehouse_id,
        "Urządzenia Wynajem",
        index,
        "Niszczarka HSM",
        Decimal(quantity),
        Decimal("0"),
        Decimal("0"),
        97,
        "HSM",
        "SECURIO",
        "NIE",
    )


def test_lookup_zachowuje_ostrzezenie_po_przygotowaniu_kartoteki() -> None:
    """Istniejąca kartoteka z dopiskiem pozwala finalizować wykup mimo ostrzeżenia."""
    cursor = _LookupCursor(
        ewidencja="B630205697/BNP",
        serial="B630205697",
        warehouse_rows=[_warehouse_row("WKP/B630205697/BNP")],
    )
    lookup = _build_lookup_payload(cursor, "B630205697")

    assert lookup["can_complete"] is True
    assert lookup["can_create_catalog"] is False
    assert lookup["suggested_index"] == "WKP/B630205697/BNP"
    assert lookup["suggested_ewidencja"] == "WKP/B630205697"
    assert len(lookup["warnings"]) == 1


@pytest.mark.parametrize("source", ["WKP/4579/BNP", "wkp/B630205697", "WKP/inne oznaczenie"])
def test_lookup_blokuje_kazde_dotychczasowe_wkp(source: str) -> None:
    """Oznaczenia WKP nie uruchamiają kolejnego wykupu przez wariant serialowy."""
    lookup = _build_lookup_payload(_LookupCursor(ewidencja=source), "3101RC80528")
    assert lookup["can_create_catalog"] is False
    assert lookup["can_complete"] is False
    assert any("już oznaczenie WKP" in message for message in lookup["blockers"])
    with pytest.raises(ValueError, match="już oznaczenie WKP"):
        _build_buyout_identifiers(source, serial="B630205697")


def test_lookup_nie_dopasowuje_fragmentu_dluzszego_serialu() -> None:
    """WKP/ABC1234 nie może zostać użyte przy wykupie serialu ABC123."""
    cursor = _LookupCursor(
        ewidencja="",
        serial="ABC123",
        warehouse_rows=[_warehouse_row("WKP/ABC1234"), _warehouse_row("WKP/ABC1234/BNP")],
    )
    lookup = _build_lookup_payload(cursor, "ABC123")
    assert lookup["warehouse_rows"] == []
    assert lookup["can_create_catalog"] is True


@pytest.mark.parametrize("serial,serial2", [(" AB-12/34 ", "ALIAS"), ("", " AB-12/34 ")])
def test_identyfikator_korzysta_z_kanonicznego_serialu(serial: str, serial2: str) -> None:
    """Pierwszy serial ma pierwszeństwo, a pusty pozwala użyć SERIAL2."""
    identifiers = _build_buyout_identifiers("", serial=serial, serial2=serial2)
    assert identifiers.value == "AB1234"
    assert identifiers.ewidencja == identifiers.warehouse_index == "WKP/AB1234"


@pytest.mark.parametrize("value", ["WKP/ABC123", "wkp/abc123/BNP/SmartIT", "WKP/ABC123/"])
def test_walidacja_serialu_dopuszcza_dopiski(value: str) -> None:
    """Dopiski można edytować przy zachowaniu bazowego numeru seryjnego."""
    normalized = _validate_target_identifier(
        value, expected_value="ABC123", identifier_mode="serial", field_name="MAGAZYN.INDEKS"
    )
    assert normalized == "WKP/ABC123" + value[len("WKP/ABC123") :]


@pytest.mark.parametrize("value", ["WKP/ABC124", "WKP/ABC1234/BNP"])
def test_walidacja_blokuje_zmiane_serialu(value: str) -> None:
    with pytest.raises(ValueError, match="musi zachować numer seryjny ABC123"):
        _validate_target_identifier(
            value, expected_value="ABC123", identifier_mode="serial", field_name="MASZYNA.EWIDENCJA"
        )


def test_identyfikatory_nie_sa_skracane_do_limitu_bazy() -> None:
    """Pełny serial i dopiski przekraczające limit blokują zapis zamiast zmiany tożsamości."""
    assert _build_buyout_identifiers("", serial="A" * 96).ewidencja == "WKP/" + "A" * 96
    for serial, serial2 in [("A" * 97, ""), ("", "A" * 110)]:
        with pytest.raises(ValueError, match="100 znaków"):
            _build_buyout_identifiers("", serial=serial, serial2=serial2)
    with pytest.raises(ValueError, match="100 znaków"):
        _validate_target_identifier(
            "WKP/ABC123/" + "X" * 100,
            expected_value="ABC123",
            identifier_mode="serial",
            field_name="MAGAZYN.INDEKS",
        )
    lookup = _build_lookup_payload(
        _LookupCursor(ewidencja="", serial="", serial2="A" * 110), "A" * 110
    )
    assert lookup["can_create_catalog"] is False
    assert "100 znaków" in lookup["blockers"][0]


@pytest.mark.parametrize("machine_count", [0, 2])
def test_lookup_wymaga_jednej_maszyny(machine_count: int) -> None:
    cursor = _LookupCursor(ewidencja="", serial="ABC123")
    cursor.machine_rows *= machine_count
    lookup = _build_lookup_payload(cursor, "ABC123")
    assert lookup["machine"] is None
    assert lookup["can_create_catalog"] is False
    assert lookup["can_complete"] is False
    assert lookup["blockers"]


def test_lookup_blokuje_wiele_kartotek_i_brak_dostawcy() -> None:
    cursor = _LookupCursor(
        ewidencja="",
        serial="ABC123",
        warehouse_rows=[
            _warehouse_row("WKP/ABC123"),
            _warehouse_row("WKP/ABC123/BNP", item_id=18593),
        ],
    )
    cursor.supplier_rows = []
    lookup = _build_lookup_payload(cursor, "ABC123")
    assert lookup["can_create_catalog"] is False
    assert lookup["can_complete"] is False
    assert len(lookup["blockers"]) == 2


class _WriteCursor(_LookupCursor):
    """Symuluje skutki zapytań i przyjęcie pozycji PZ przez trigger magazynowy."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.documents: list[dict] = []
        self.positions: list[tuple] = []
        self.statements: list[tuple[str, tuple]] = []
        self.quantity_increment = Decimal("1")

    def execute(self, sql: str, _params=None) -> None:
        statement = " ".join(sql.split())
        parameters = tuple(_params or ())
        self.statements.append((statement, parameters))
        self.result = []
        if statement.startswith("INSERT INTO MAGAZYN"):
            self.warehouse_rows.append(
                (
                    18592,
                    parameters[2],
                    "Urządzenia Wynajem",
                    parameters[4],
                    parameters[5],
                    parameters[7],
                    Decimal("0"),
                    Decimal("0"),
                    parameters[11],
                    parameters[9],
                    parameters[10],
                    parameters[12],
                )
            )
            self.result = [(18592,)]
        elif statement.startswith("UPDATE MAGAZYN"):
            self.warehouse_rows = [
                (
                    row[:3] + (parameters[0], parameters[1]) + row[5:]
                    if row[0] == parameters[5]
                    else row
                )
                for row in self.warehouse_rows
            ]
        elif statement.startswith("INSERT INTO ZAKUPY"):
            self.documents.append(
                {
                    "id": 37904,
                    "number": parameters[10],
                    "external_document": parameters[13],
                    "supplier_id": parameters[2],
                }
            )
            self.result = [(37904,)]
        elif statement.startswith("INSERT INTO ZAKPOZYCJA"):
            self.positions.append(parameters)
            self.warehouse_rows = [
                (
                    row[:5] + (row[5] + self.quantity_increment,) + row[6:]
                    if row[0] == parameters[3]
                    else row
                )
                for row in self.warehouse_rows
            ]
            self.result = [(107982,)]
        elif statement.startswith("UPDATE MASZYNA"):
            self.machine_rows = [
                row[:10] + (parameters[0],) + row[11:] if row[0] == parameters[1] else row
                for row in self.machine_rows
            ]
        elif "FROM ZAKUPY z" in statement:
            for document in self.documents:
                for position in self.positions:
                    if position[0] != document["id"] or document["supplier_id"] != parameters[0]:
                        continue
                    if len(parameters) == 2:
                        if document["external_document"].upper() == parameters[1]:
                            self.result.append(
                                (
                                    document["id"],
                                    document["number"],
                                    107982,
                                    position[3],
                                    position[10],
                                )
                            )
                    elif position[10].upper() == parameters[1] or position[10].upper().startswith(
                        parameters[2]
                    ):
                        self.result.append(
                            (
                                document["id"],
                                document["number"],
                                document["external_document"],
                                107982,
                                position[3],
                                position[10],
                            )
                        )
        elif statement.startswith("SELECT FIRST 1 ID_MAGAZYN_TABLE"):
            self.result = [
                (row[0],)
                for row in self.warehouse_rows
                if row[1] == parameters[0]
                and row[3].upper() == parameters[1]
                and row[0] != parameters[2]
            ]
        elif statement.startswith("SELECT COALESCE(ILOSC, 0)"):
            self.result = [(row[5],) for row in self.warehouse_rows if row[0] == parameters[0]]
        else:
            super().execute(sql, _params)


class _WriteConnection:
    """Przechowuje stan zatwierdzony i przywraca go przy wycofaniu transakcji."""

    state_fields = ("machine_rows", "warehouse_rows", "documents", "positions")

    def __init__(self, cursor: _WriteCursor) -> None:
        self.write_cursor = cursor
        self.commits = 0
        self.rollbacks = 0
        self.snapshot: dict = {}

    def cursor(self) -> _WriteCursor:
        self.snapshot = {
            name: deepcopy(getattr(self.write_cursor, name)) for name in self.state_fields
        }
        return self.write_cursor

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1
        for name, value in self.snapshot.items():
            setattr(self.write_cursor, name, deepcopy(value))

    def close(self) -> None:
        pass


@pytest.fixture
def write_connection(monkeypatch):
    """Izoluje wykup od Firebirda, zachowując testowanie rzeczywistych funkcji zapisu."""
    cursor = _WriteCursor(ewidencja="B630205697/BNP", serial="B630205697")
    connection = _WriteConnection(cursor)
    monkeypatch.setattr(buyout_service, "_firebird_connection", lambda: connection)
    monkeypatch.setattr(buyout_service, "_acquire_firebird_write_lock", lambda cursor: None)
    monkeypatch.setattr(buyout_service, "_ensure_intake_generators", lambda cursor: None)
    monkeypatch.setattr(buyout_service, "_next_pz_document_number", lambda cursor, year: 245)
    return connection


def _catalog_arguments(cursor: _WriteCursor) -> dict:
    """Tworzy żądanie przygotowania kartoteki na podstawie podglądu."""
    serial = cursor.machine_rows[0][8] or cursor.machine_rows[0][9]
    lookup = _build_lookup_payload(cursor, serial)
    return {
        "serial": serial,
        "machine_table_id": 5189,
        "expected_ewidencja": cursor.machine_rows[0][10],
        "warehouse_index": lookup["suggested_index"],
        "item_name": "Niszczarka HSM",
        "kto": "CTIP/OPERATOR",
    }


def _complete_arguments(cursor: _WriteCursor, **overrides) -> dict:
    """Dodaje dane faktury do parametrów finalizacji wykupu."""
    parameters = _catalog_arguments(cursor)
    lookup = _build_lookup_payload(cursor, parameters["serial"])
    return {
        **parameters,
        "warehouse_item_id": 18592,
        "target_ewidencja": lookup["suggested_ewidencja"],
        "external_document": "FWK26/09/00025",
        "document_date": date(2026, 9, 10),
        "purchase_price_netto": Decimal("99.40"),
        "issued_by": "OPERATOR",
        **overrides,
    }


@pytest.mark.parametrize(
    "serial,source,document,price,target,index",
    [
        (
            "B630205697",
            "B630205697/BNP",
            "FWK26/09/00025",
            "99.40",
            "WKP/B630205697",
            "WKP/B630205697",
        ),
        ("C630099408", "C630099408", "FWK26/09/00038", "24.00", "WKP/C630099408", "WKP/C630099408"),
        ("B630205697", "", "TEST/PUSTA", "99.40", "WKP/B630205697", "WKP/B630205697"),
        (
            "B630205697",
            "KP/4579/SRS/SmartIT",
            "TEST/KP",
            "99.40",
            "WKP/4579/SRS/SmartIT",
            "WKP/4579/BNP",
        ),
    ],
)
def test_pelny_wykup_i_ponowienie_nie_dubluja_pz(
    write_connection, serial, source, document, price, target, index
) -> None:
    """Przygotowanie stanu 0 i finalizacja stanu 1 działają dla obu faktur oraz KP."""
    cursor = write_connection.write_cursor
    cursor.machine_rows[0] = (
        cursor.machine_rows[0][:8] + (serial, "", source) + cursor.machine_rows[0][11:]
    )
    catalog_parameters = _catalog_arguments(cursor)
    catalog = create_bnp_catalog_item(**catalog_parameters)
    assert catalog.created is True
    assert catalog.warehouse_item["index"] == index
    assert catalog.warehouse_item["quantity"] == 0
    assert cursor.machine_rows[0][10] == source
    assert cursor.documents == cursor.positions == []

    reused = create_bnp_catalog_item(**catalog_parameters)
    assert reused.created is False
    assert len(cursor.warehouse_rows) == 1
    assert write_connection.commits == 1
    complete_parameters = _complete_arguments(
        cursor, external_document=document, purchase_price_netto=Decimal(price)
    )
    result = complete_bnp_buyout(**complete_parameters)
    assert result.already_completed is False
    assert result.target_ewidencja == target
    assert result.warehouse_index == index
    assert result.warehouse_quantity == 1
    assert cursor.machine_rows[0][10] == target
    assert len(cursor.documents) == len(cursor.positions) == 1
    assert cursor.positions[0][10] == index
    assert cursor.positions[0][12] == Decimal(price)
    assert cursor.positions[0][13] == 1
    assert cursor.positions[0][7] == 27
    assert not any(
        statement.startswith("INSERT INTO SERIAL") for statement, _parameters in cursor.statements
    )

    retried = complete_bnp_buyout(**complete_parameters)
    assert retried.already_completed is True
    assert retried.pz_id == result.pz_id
    assert len(cursor.documents) == len(cursor.positions) == 1
    assert cursor.warehouse_rows[0][5] == 1
    assert write_connection.commits == 2
    lookup = _build_lookup_payload(cursor, serial)
    assert lookup["can_complete"] is False
    assert any("już oznaczenie WKP" in message for message in lookup["blockers"])


def test_finalizacja_dopuszcza_niezalezne_dopiski_i_serial2(write_connection) -> None:
    """Wyszukiwanie przez alias nadal chroni główny serial i dopuszcza dopiski."""
    cursor = write_connection.write_cursor
    cursor.machine_rows[0] = (
        cursor.machine_rows[0][:9] + ("ALIAS123",) + cursor.machine_rows[0][10:]
    )
    catalog_parameters = _catalog_arguments(cursor)
    catalog_parameters.update(serial="ALIAS123", warehouse_index="WKP/B630205697/BNP")
    create_bnp_catalog_item(**catalog_parameters)
    parameters = _complete_arguments(
        cursor, serial="ALIAS123", target_ewidencja="WKP/B630205697/Serwis"
    )
    result = complete_bnp_buyout(**parameters)
    assert result.target_ewidencja == "WKP/B630205697/Serwis"
    assert result.warehouse_index == "WKP/B630205697/BNP"


@pytest.mark.parametrize("increment", [Decimal("0"), Decimal("2")])
def test_blad_stanu_wycofuje_caly_zapis_pz(write_connection, increment) -> None:
    """Niepoprawny skutek triggera cofa dokument, pozycję oraz ewidencję maszyny."""
    cursor = write_connection.write_cursor
    create_bnp_catalog_item(**_catalog_arguments(cursor))
    cursor.quantity_increment = increment
    parameters = _complete_arguments(cursor, target_ewidencja="WKP/B630205697/BNP")
    with pytest.raises(RuntimeError, match="transakcja została wycofana"):
        complete_bnp_buyout(**parameters)
    assert cursor.documents == cursor.positions == []
    assert cursor.machine_rows[0][10] == "B630205697/BNP"
    assert cursor.warehouse_rows[0][5] == 0
    assert write_connection.commits == 1
    assert write_connection.rollbacks == 1


@pytest.mark.parametrize("operation", ["catalog", "complete"])
def test_zapis_blokuje_zmiane_ewidencji_od_wyszukania(write_connection, operation) -> None:
    """Równoległa zmiana ewidencji wymaga nowego podglądu także przy pustym polu."""
    cursor = write_connection.write_cursor
    if operation == "catalog":
        parameters = _catalog_arguments(cursor)
        function = create_bnp_catalog_item
    else:
        create_bnp_catalog_item(**_catalog_arguments(cursor))
        parameters = _complete_arguments(cursor)
        function = complete_bnp_buyout
    parameters["expected_ewidencja"] = ""
    with pytest.raises(ValueError, match="zmieniła się od czasu wyszukania"):
        function(**parameters)
    assert cursor.documents == cursor.positions == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("target_ewidencja", "WKP/INNY"),
        ("warehouse_index", "WKP/B6302056970/BNP"),
        ("serial", "---"),
        ("serial", "INNY"),
        ("warehouse_item_id", 18593),
    ],
)
def test_finalizacja_odrzuca_podmienione_identyfikatory(write_connection, field, value) -> None:
    cursor = write_connection.write_cursor
    create_bnp_catalog_item(**_catalog_arguments(cursor))
    parameters = _complete_arguments(cursor, **{field: value})
    with pytest.raises(ValueError):
        complete_bnp_buyout(**parameters)
    assert cursor.documents == cursor.positions == []
    assert cursor.warehouse_rows[0][5] == 0


@pytest.mark.parametrize("quantity", ["1", "-1"])
def test_zapis_blokuje_stan_rozny_od_zera(write_connection, quantity) -> None:
    cursor = write_connection.write_cursor
    cursor.warehouse_rows = [_warehouse_row("WKP/B630205697", quantity)]
    with pytest.raises(ValueError, match="stan różny od 0"):
        create_bnp_catalog_item(**_catalog_arguments(cursor))
    with pytest.raises(ValueError, match="stan różny od 0"):
        complete_bnp_buyout(**_complete_arguments(cursor))
    assert cursor.documents == cursor.positions == []


@pytest.mark.parametrize("same_document", [True, False])
def test_finalizacja_blokuje_istniejacy_dokument_lub_wczesniejszy_wykup(
    write_connection, same_document
) -> None:
    """Ochrona wcześniejszego PZ działa także po ręcznym przywróceniu starej ewidencji."""
    cursor = write_connection.write_cursor
    create_bnp_catalog_item(**_catalog_arguments(cursor))
    parameters = _complete_arguments(cursor)
    complete_bnp_buyout(**parameters)
    cursor.machine_rows[0] = (
        cursor.machine_rows[0][:10] + ("B630205697/BNP",) + cursor.machine_rows[0][11:]
    )
    cursor.warehouse_rows[0] = (
        cursor.warehouse_rows[0][:5] + (Decimal("0"),) + cursor.warehouse_rows[0][6:]
    )
    if not same_document:
        parameters["external_document"] = "INNA/FAKTURA"
    expected_error = "Dokument BNP" if same_document else "istnieje już PZ wykupu BNP"
    with pytest.raises(ValueError, match=expected_error):
        complete_bnp_buyout(**parameters)
    assert len(cursor.documents) == len(cursor.positions) == 1
