"""Izolowane testy odczytu ORBIT z atrapą kursora; źródła pozostają niezmienione."""

import json
import re
import sqlite3
from contextlib import contextmanager
from copy import deepcopy
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from app.services import orbit_source as source

SCHEMA = {
    "MASZYNA": "ID_MASZYNA_TABLE ID_MASZYNA ID_KLIENT ID_MODEL ID_UMOWACPC SERIAL MODEL AKTYWNA UM_P UM_K",
    "MODEL": "ID_MODEL MARKA MODEL",
    "KLIENT": "ID_KLIENT NAZWA",
    "UMOWACPC": "ID_UMOWACPC_TABLE ID_UMOWACPC ID_KLIENT UMOWA AKTYWNA U_START U_STOP",
    "UMOWA": "ID_UMOWA ID_MASZYNA ID_KLIENT NUMER DATA DATA_START DATA_STOP",
    "CPC": "ID_CPC_TABLE ID_MASZYNA ID_KLIENT ID_UMOWACPC ID_FAKTURA ROK MIESIAC LICZNIK_MONO_START LICZNIK_MONO_END KOPIE_MONO OPLATA_MONO OPLATA_SUMA KOSZTY",
    "ZLECENIE": "ID_ZLECENIE_TABLE ID_MASZYNA ID_KLIENT ID_ZLECENIE ROK ID_FAKTURA ID_RW ID_WZ DATA DATA_Z STAN RODZAJ_US PROBLEM WYKONANIE PRZESYLKA PRZESYLKA_WE DATA_PRZES DATA_PRZES_WE PASS SIGNATURE_BLOB",
    "ZPOZYCJA": "ID_ZPOZYCJA_TABLE ID_MASZYNA ID_KLIENT ID_ZLECENIE ROK ID_MAGPOZ ID_SERIAL NAZWA ILOSC POBRANO CENA WARTOSC CENA_Z WARTOSC_Z",
    "ZAKUPY": "ID_ZAKUPY_TABLE ID_FIRMA ID_KLIENT ID_ZLECENIE ROK_ZLECENIA RODZAJ_DOK NUMER ID_MW DATA_WYST DATA_PRZY_WYDA",
    "ZAKPOZYCJA": "ID_ZAKPOZYCJA_TABLE ID_ZAKUPY ID_FIRMA ID_KLIENT ID_MAGAZYN ID_SERIAL NUMER DATA_PRZY_WYDA RODZAJ_DOK NAZWA ILOSC POBRANO CENA_NETTO WARTOSC_NETTO CENA_Z WARTOSC_Z",
    "FAKTURA": "ID_FAKTURA_TABLE ID_FIRMA ID_MASZYNA ID_KLIENT ID_ZLECENIE ROK_ZLECENIA ID_UMOWACPC NUMER RODZAJ_DOK NR_KORY KOREKTA KORID DATA_WYST SUMA_NETTO CCODE",
    "FPOZYCJA": "ID_FPOZYCJA_TABLE ID_FAKTURA ID_MASZYNA ID_ZLECENIE ROK_ZLECENIA ID_OLD NAZWA ILOSC CENA_NETTO WARTOSC_NETTO KOREKTA ILOSC_KOR WARTOSC_NETTO_KOR",
    "SERIAL": "ID_SERIAL ID_MASZYNA ID_MAGPOZ ID_MAGAZYN ID_WZ ID_RW ID_FAKTURA SERIAL DATA_SPRZ STAN",
    "MAGAZYN": "ID_MAGAZYN_TABLE ID_MAGAZYN ILOSC",
}


class FakeCursor:
    """Wykonuje wyłącznie SELECT na syntetycznych tabelach i emuluje FIRST Firebird."""

    def __init__(self, tables):
        """Tworzy odizolowaną pamięć SQLite jako mechanizm oceny relacji zapytań."""
        self.database = sqlite3.connect(":memory:")
        self.queries = []
        self.closed = False
        self.failed_table = None
        self.result = iter(())
        for table, fields in SCHEMA.items():
            columns = fields.split()
            definitions = ",".join(
                f"{field} {'INTEGER' if field.startswith('ID_') or field in ('ROK', 'ROK_ZLECENIA', 'MIESIAC') else 'TEXT'}"
                for field in columns
            )
            self.database.execute(f"CREATE TABLE {table} ({definitions})")
            for row in tables.get(table, []):
                values = [row.get(field) for field in columns]
                values = [
                    str(value) if isinstance(value, (Decimal, date)) else value for value in values
                ]
                self.database.execute(
                    f"INSERT INTO {table} VALUES ({','.join('?' for _ in columns)})", values
                )

    def execute(self, sql, params=()):
        """Odrzuca zapis oraz rejestruje parametry i rzeczywistą treść zapytań źródła."""
        assert sql.lstrip().upper().startswith("SELECT ")
        self.queries.append((sql, tuple(params)))
        if "RDB$RELATION_FIELDS" in sql:
            assert "RDB$FIELD_TYPE<>261" in sql
            assert "RDB$DIMENSIONS IS NULL" in sql
            self.result = iter(
                (table, field)
                for table, fields in SCHEMA.items()
                for field in fields.split()
                if not field.endswith("_BLOB")
            )
            return
        if self.failed_table and f"FROM {self.failed_table} t" in sql:
            raise RuntimeError("Kontrolowana awaria odczytu.")
        limit = re.match(r"SELECT FIRST (\d+) ", sql)
        if limit:
            sql = "SELECT " + sql[limit.end() :] + f" LIMIT {limit.group(1)}"
        self.result = iter(self.database.execute(sql, params))

    def fetchmany(self, size):
        """Zwraca ograniczoną porcję; implementacja nie udostępnia fetchall."""
        result = []
        for _ in range(size):
            row = next(self.result, None)
            if row is None:
                break
            result.append(row)
        return result

    def close(self):
        """Zamyka wyłącznie zasób atrapy kursora."""
        self.closed = True
        self.database.close()


def sample_tables():
    """Buduje historię dwóch maszyn, dwóch umów i jednej wspólnej faktury."""
    tables = {table: [] for table in SCHEMA}
    tables.update(
        MASZYNA=[
            {
                "ID_MASZYNA_TABLE": 170,
                "ID_MASZYNA": 17,
                "ID_KLIENT": 42,
                "ID_MODEL": 2,
                "ID_UMOWACPC": 8,
                "SERIAL": "ORBIT-17",
                "AKTYWNA": "TAK",
                "UM_P": "2020-01-01",
            },
            {
                "ID_MASZYNA_TABLE": 180,
                "ID_MASZYNA": 18,
                "ID_KLIENT": 42,
                "ID_MODEL": 2,
                "ID_UMOWACPC": 8,
                "SERIAL": "ORBIT-18",
                "AKTYWNA": "TAK",
                "UM_P": "2020-01-01",
            },
        ],
        MODEL=[{"ID_MODEL": 2, "MARKA": "Test", "MODEL": "Model testowy"}],
        KLIENT=[{"ID_KLIENT": 42, "NAZWA": "Klient testowy"}],
        UMOWACPC=[
            {
                "ID_UMOWACPC_TABLE": 7,
                "ID_UMOWACPC": 700,
                "ID_KLIENT": 41,
                "UMOWA": "Historyczna",
                "AKTYWNA": "NIE",
                "U_START": "2009-01-01",
                "U_STOP": "2019-12-31",
            },
            {
                "ID_UMOWACPC_TABLE": 8,
                "ID_UMOWACPC": 800,
                "ID_KLIENT": 42,
                "UMOWA": "Bieżąca",
                "AKTYWNA": "TAK",
                "U_START": "2020-01-01",
            },
        ],
        CPC=[
            {
                "ID_CPC_TABLE": 1,
                "ID_MASZYNA": 17,
                "ID_KLIENT": 41,
                "ID_UMOWACPC": 7,
                "ROK": 2010,
                "MIESIAC": 1,
                "OPLATA_MONO": Decimal("0.0350"),
            },
            {
                "ID_CPC_TABLE": 2,
                "ID_MASZYNA": 17,
                "ID_KLIENT": 42,
                "ID_UMOWACPC": 8,
                "ID_FAKTURA": 900,
                "ROK": 2026,
                "MIESIAC": 9,
            },
            {
                "ID_CPC_TABLE": 3,
                "ID_MASZYNA": 18,
                "ID_KLIENT": 42,
                "ID_UMOWACPC": 8,
                "ID_FAKTURA": 900,
                "ROK": 2026,
                "MIESIAC": 9,
            },
        ],
        ZLECENIE=[
            {
                "ID_ZLECENIE_TABLE": 700,
                "ID_MASZYNA": 17,
                "ID_KLIENT": 42,
                "ID_ZLECENIE": 77,
                "ROK": 2026,
                "ID_RW": 300,
                "DATA": "2026-09-10",
                "RODZAJ_US": "Umowa CPC",
                "PRZESYLKA": "PACZKA-TEST",
                "DATA_PRZES": "2026-09-11",
                "PASS": "sekret-testowy",
                "SIGNATURE_BLOB": b"dane-binarne",
            }
        ],
        ZPOZYCJA=[
            {
                "ID_ZPOZYCJA_TABLE": 70,
                "ID_MASZYNA": 17,
                "ID_KLIENT": 42,
                "ID_ZLECENIE": 77,
                "ROK": 2026,
                "ID_MAGPOZ": 555,
                "NAZWA": "Materiał testowy",
                "ILOSC": Decimal("2.0000"),
                "POBRANO": Decimal("2.0000"),
                "CENA": Decimal("0.0000"),
                "WARTOSC": Decimal("0.0000"),
                "CENA_Z": Decimal("120.0000"),
                "WARTOSC_Z": Decimal("240.0000"),
            }
        ],
        ZAKUPY=[
            {
                "ID_ZAKUPY_TABLE": 300,
                "ID_FIRMA": 1,
                "ID_KLIENT": 42,
                "ID_ZLECENIE": 77,
                "ROK_ZLECENIA": 2026,
                "RODZAJ_DOK": "RW",
                "NUMER": "RW / 12 / 2026",
                "ID_MW": 28,
                "DATA_WYST": "2026-09-10",
                "DATA_PRZY_WYDA": "2026-09-10",
            }
        ],
        ZAKPOZYCJA=[
            {
                "ID_ZAKPOZYCJA_TABLE": 321,
                "ID_ZAKUPY": 300,
                "ID_KLIENT": 42,
                "ID_MAGAZYN": 555,
                "RODZAJ_DOK": "RW",
                "NAZWA": "Materiał testowy",
                "ILOSC": Decimal("2.0000"),
                "POBRANO": Decimal("2.0000"),
                "CENA_NETTO": Decimal("0.0000"),
                "WARTOSC_NETTO": Decimal("0.0000"),
                "CENA_Z": Decimal("120.0000"),
                "WARTOSC_Z": Decimal("240.0000"),
            }
        ],
        FAKTURA=[
            {
                "ID_FAKTURA_TABLE": 900,
                "ID_FIRMA": 1,
                "ID_KLIENT": 42,
                "ID_UMOWACPC": 8,
                "NUMER": "FV/900/2026",
                "RODZAJ_DOK": "KPSK",
                "DATA_WYST": "2026-09-12",
                "SUMA_NETTO": Decimal("150.0000"),
                "CCODE": "PLN",
            }
        ],
        FPOZYCJA=[
            {
                "ID_FPOZYCJA_TABLE": 901,
                "ID_FAKTURA": 900,
                "ID_MASZYNA": 17,
                "NAZWA": "Opłata A",
                "WARTOSC_NETTO": Decimal("50.0000"),
            },
            {
                "ID_FPOZYCJA_TABLE": 902,
                "ID_FAKTURA": 900,
                "ID_MASZYNA": 18,
                "NAZWA": "Opłata B",
                "WARTOSC_NETTO": Decimal("100.0000"),
            },
        ],
    )
    return tables


@pytest.fixture
def snapshot(monkeypatch):
    """Podmienia całe połączenie MS, konfigurację i sprawdzanie sieci na atrapy."""
    state = SimpleNamespace(tables=sample_tables(), cursors=[], connections=[], failed_table=None)
    monkeypatch.setattr(source, "PAGE_SIZE", 2)
    monkeypatch.setattr(source, "BATCH_SIZE", 2)
    monkeypatch.setattr(source, "ID_BATCH_SIZE", 2)
    monkeypatch.setattr(source, "check_test_host", lambda host: None)
    monkeypatch.setattr(
        source,
        "settings",
        SimpleNamespace(
            fb_host="127.0.0.1",
            shipping_orbit_scrap_customer_ids=[],
            shipping_orbit_scrap_warehouse_ids=[],
        ),
    )
    config = SimpleNamespace(ms_user="test_ro", ms_password=SecretStr("test"))
    monkeypatch.setattr(source, "TelemetrySettings", lambda: config)
    state.config = config

    @contextmanager
    def connection(*, config):
        """Udostępnia atrapę, nigdy rzeczywiste połączenie ani zapis źródłowy."""
        state.connections.append(config)
        cursor = FakeCursor(deepcopy(state.tables))
        cursor.failed_table = state.failed_table
        state.cursors.append(cursor)
        yield SimpleNamespace(cursor=lambda: cursor)

    monkeypatch.setattr(source, "firebird_connection", connection)
    state.load = source.load_orbit_snapshot
    return state


def test_invoice_candidates_use_snapshot_index_without_scanning_fleet(monkeypatch):
    """Powiązanie faktury nie odtwarza historii umów dla każdej maszyny."""
    tables = sample_tables()
    normalizer = source._Normalizer(tables)
    normalizer.devices(set())
    invoice = {"ID_FAKTURA_TABLE": 987654, "ID_UMOWACPC": 8}
    expected = {
        machine_id for machine_id in normalizer.first if 8 in normalizer.contract_ids(machine_id)
    }
    assert expected

    def forbidden_scan(machine_id):
        """Wykrywa kosztowne przeliczanie indeksu podczas obsługi dokumentu."""
        raise AssertionError("Ponowne skanowanie historii umów floty")

    monkeypatch.setattr(normalizer, "contract_ids", forbidden_scan)
    assert normalizer.invoice_candidates(invoice) == expected
    normalizer.first.clear()
    assert normalizer.invoice_candidates(invoice) == set()


def fact(result, key):
    """Wyszukuje jeden fakt po stabilnym kluczu źródłowym."""
    return next(item for item in result["facts"] if item["external_key"] == key)


def test_full_history_pages_readonly_config_and_json(snapshot):
    """Potwierdza pełne CPC, techniczne klucze, strony, brak sekretów i poprawny JSON."""
    result = snapshot.load()
    assert result["complete"] is True
    assert [item["id"] for item in result["devices"]] == ["ms:17", "ms:18"]
    assert fact(result, "CPC:1")["data"]["raw"]["CPC"][0]["OPLATA_MONO"] == "0.0350"
    assert fact(result, "CPC:1")["observed_at"] is None
    assert fact(result, "CPC:1")["time_precision"] == "month"
    assert fact(result, "UMOWACPC:7:MASZYNA:17")["observed_at"] == "2009-01-01"
    assert snapshot.connections == [snapshot.config]
    assert snapshot.cursors[0].closed
    queries = snapshot.cursors[0].queries
    assert any("FROM CPC t" in sql for sql, _ in queries)
    assert not any("ORDER BY t.ID_FPOZYCJA_TABLE" in sql for sql, _ in queries)
    assert not any("SELECT FIRST" in sql for sql, _ in queries)
    assert all("PASS" not in sql and "SIGNATURE_BLOB" not in sql for sql, _ in queries)
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
    assert "sekret-testowy" not in encoded
    assert "SIGNATURE_BLOB" not in encoded


def test_known_ids_extend_fleet_and_missing_machine_survives(snapshot):
    """Brak bieżącej umowy zawiesza znaną maszynę, a brak kartoteki wymaga przeglądu."""
    snapshot.tables["MASZYNA"].append(
        {"ID_MASZYNA_TABLE": 190, "ID_MASZYNA": 19, "ID_KLIENT": 42, "SERIAL": "ORBIT-19"}
    )
    result = snapshot.load([19, 99])
    statuses = {item["ms_machine_id"]: item["status"] for item in result["devices"]}
    assert statuses == {17: "active", 18: "active", 19: "suspended", 99: "review"}


def test_pilot_restricts_fleet_known_ids_and_projected_facts(snapshot):
    """Pilot nie pobiera całej floty ani nie projektuje urządzeń wspólnej faktury."""
    result = snapshot.load([18, 99], only_machine_ids=[17])
    assert [item["id"] for item in result["devices"]] == ["ms:17"]
    assert {item["device_id"] for item in result["facts"]} == {"ms:17"}
    machine_queries = [
        (sql, params) for sql, params in snapshot.cursors[0].queries if "FROM MASZYNA t" in sql
    ]
    assert all(
        "t.ID_MASZYNA IN (?)" in sql and 18 not in params and 99 not in params
        for sql, params in machine_queries
    )
    assert fact(result, "FAKTURA:900:MASZYNA:17")["data"]["net_value"] == "50.0000"


def test_empty_pilot_does_not_connect(snapshot):
    """Pusta lista pilota jest pustym zakresem, a nie całą flotą."""
    assert snapshot.load([17], only_machine_ids=[]) == {
        "devices": [],
        "facts": [],
        "complete": True,
    }
    assert not snapshot.connections


@pytest.mark.parametrize("identifier", [0, -1, True, "17 OR 1=1", 1.5])
def test_invalid_ids_fail_before_connecting(snapshot, identifier):
    """Błędne ID nie trafiają do zapytań ani połączenia."""
    with pytest.raises(ValueError):
        snapshot.load([identifier])
    assert not snapshot.connections


def test_purchase_cost_uses_historical_buy_prices_not_zero_sale(snapshot):
    """Koszt kontraktowy pochodzi z uzgodnionego zakupu mimo zerowej sprzedaży."""
    item = fact(snapshot.load(), "ZAKPOZYCJA:321")
    data = item["data"]
    assert item["status"] == "confirmed"
    assert data["is_contract"] is True
    assert data["contract_id"] == 8
    assert data["purchase_price"] == data["document_purchase_price"] == "120.0000"
    assert data["purchase_value"] == "240.0000"
    assert data["issued_quantity"] == "2.0000"
    assert data["returned_quantity"] is None
    assert data["currency"] is None
    assert data["zero_cost_confirmed"] is False


@pytest.mark.parametrize(
    "table,field,value,expected",
    [
        ("ZPOZYCJA", "CENA_Z", None, "missing"),
        ("ZPOZYCJA", "WARTOSC_Z", None, "missing"),
        ("ZAKPOZYCJA", "CENA_Z", None, "missing"),
        ("ZAKPOZYCJA", "WARTOSC_Z", "239", "conflict"),
        ("ZAKPOZYCJA", "POBRANO", None, "missing"),
        ("ZAKPOZYCJA", "POBRANO", "0", "conflict"),
        ("ZAKPOZYCJA", "POBRANO", "1", "conflict"),
        ("ZAKPOZYCJA", "ILOSC", "-2", "conflict"),
    ],
)
def test_missing_or_conflicting_evidence_blocks_cost(snapshot, table, field, value, expected):
    """Niepełne ceny, plan i niejasny zwrot nigdy nie stają się pełnym kosztem."""
    snapshot.tables[table][0][field] = value
    item = fact(snapshot.load(), "ZAKPOZYCJA:321")
    assert item["status"] == expected
    assert item["data"]["purchase_value"] is None
    assert item["data"]["purchase_cost"]["status"] == expected


def test_partial_issue_does_not_charge_planned_quantity(snapshot):
    """Koszt potwierdzonego częściowego wydania nie zawiera niezrealizowanej reszty planu."""
    snapshot.tables["ZPOZYCJA"][0].update(ILOSC="5.0000", WARTOSC_Z="600.0000")
    data = fact(snapshot.load(), "ZAKPOZYCJA:321")["data"]
    assert data["status"] == "confirmed"
    assert data["planned_quantity"] == "5.0000"
    assert data["issued_quantity"] == "2.0000"
    assert data["purchase_value"] == "240.0000"


def test_zero_purchase_fields_do_not_prove_free_acquisition(snapshot):
    """Zgodne techniczne zera w obu tabelach nie potwierdzają bezpłatnego zakupu."""
    for table in ("ZPOZYCJA", "ZAKPOZYCJA"):
        snapshot.tables[table][0].update(CENA_Z="0.0000", WARTOSC_Z="0.0000")
    data = fact(snapshot.load(), "ZAKPOZYCJA:321")["data"]
    assert data["zero_cost_confirmed"] is False
    assert data["purchase_value"] is None
    assert "unconfirmed_zero_purchase_cost" in data["issues"]


def test_order_number_is_not_reused_across_years(snapshot):
    """Ten sam numer z innego roku nie dostarcza części ani cen do kosztu."""
    snapshot.tables["ZPOZYCJA"][0]["ROK"] = 2025
    item = fact(snapshot.load(), "ZAKPOZYCJA:321")
    assert item["status"] == "missing"
    assert "missing_order_part" in item["data"]["issues"]


def test_duplicate_order_or_part_does_not_pick_first(snapshot):
    """Duplikat pary numer/rok blokuje koszt nawet przy identycznych kwotach."""
    duplicate = dict(snapshot.tables["ZLECENIE"][0], ID_ZLECENIE_TABLE=701)
    snapshot.tables["ZLECENIE"].append(duplicate)
    item = fact(snapshot.load(), "ZAKPOZYCJA:321")
    assert item["status"] == "conflict"
    assert item["data"]["purchase_value"] is None


def test_invoice_allocates_lines_not_document_total(snapshot):
    """Wspólna faktura daje każdej maszynie tylko jej własne linie."""
    result = snapshot.load()
    first = fact(result, "FAKTURA:900:MASZYNA:17")["data"]
    second = fact(result, "FAKTURA:900:MASZYNA:18")["data"]
    assert first["net_value"] == "50.0000"
    assert second["net_value"] == "100.0000"
    assert [row["invoice_line_id"] for row in first["lines"]] == [901]
    assert "SUMA_NETTO" not in {key for key in first if key != "raw"}


def test_ambiguous_invoice_description_does_not_allocate_by_serial(snapshot):
    """Nazwa zawierająca serial nie zastępuje strukturalnego klucza linii."""
    snapshot.tables["FPOZYCJA"][0].update(ID_MASZYNA=None, NAZWA="Opłata ORBIT-17")
    item = fact(snapshot.load(only_machine_ids=[17]), "FAKTURA:900:MASZYNA:17")
    assert item["status"] == "missing"
    assert item["data"]["net_value"] is None
    assert item["data"]["unassigned_line_ids"] == [901]


def test_correction_links_number_and_firm_but_does_not_guess_delta(snapshot):
    """KORID i ID_OLD nie są zgadywanymi kluczami; korekta pozostaje konfliktem kwoty."""
    snapshot.tables["FAKTURA"].append(
        {
            "ID_FAKTURA_TABLE": 910,
            "ID_FIRMA": 1,
            "ID_KLIENT": 42,
            "NUMER": "KOR/1/2026",
            "RODZAJ_DOK": "KOR",
            "NR_KORY": "FV/900/2026",
            "KORID": 123456,
            "DATA_WYST": "2026-09-13",
            "SUMA_NETTO": "20.0000",
        }
    )
    snapshot.tables["FPOZYCJA"].append(
        {
            "ID_FPOZYCJA_TABLE": 911,
            "ID_FAKTURA": 910,
            "ID_MASZYNA": 17,
            "ID_OLD": 888888,
            "WARTOSC_NETTO": "20.0000",
            "WARTOSC_NETTO_KOR": "30.0000",
        }
    )
    item = fact(snapshot.load(), "FAKTURA:910:MASZYNA:17")
    assert item["kind"] == "correction"
    assert item["status"] == "conflict"
    assert item["data"]["original_invoice_ids"] == [900]
    assert item["data"]["net_value"] is None
    assert item["data"]["raw"]["FPOZYCJA"][0]["ID_OLD"] == 888888


def test_configured_scrap_customer_and_warehouse(snapshot):
    """Złomowanie wymaga konfiguracji; propozycja magazynu nie wystarcza bez niej."""
    snapshot.tables["MASZYNA"][0]["ID_UMOWACPC"] = None
    assert snapshot.load([17])["devices"][0]["status"] == "suspended"
    source.settings.shipping_orbit_scrap_customer_ids = [42]
    result = snapshot.load([17])
    assert result["devices"][0]["status"] == "scrapped"
    assert result["devices"][1]["status"] == "review"
    source.settings.shipping_orbit_scrap_customer_ids = []
    source.settings.shipping_orbit_scrap_warehouse_ids = [99]
    snapshot.tables["SERIAL"].append(
        {"ID_SERIAL": 1, "ID_MASZYNA": 17, "ID_MAGPOZ": 555, "ID_MAGAZYN": 99, "SERIAL": "ORBIT-17"}
    )
    snapshot.tables["MAGAZYN"].append(
        {"ID_MAGAZYN_TABLE": 555, "ID_MAGAZYN": 99, "ILOSC": "1.0000"}
    )
    device = snapshot.load([17])["devices"][0]
    assert device["status"] == "scrapped"
    assert device["data"]["currentwarehouse"]["ids"] == [99]


def test_failure_never_returns_complete_snapshot(snapshot):
    """Awaria jednej tabeli przerywa odczyt i zamyka kursor zamiast publikować część."""
    snapshot.failed_table = "FPOZYCJA"
    with pytest.raises(RuntimeError, match="Kontrolowana"):
        snapshot.load()
    assert snapshot.cursors[0].closed


def test_incomplete_readonly_credentials_fail_before_connecting(snapshot):
    """Niepełne konto telemetrii nie powoduje cichego przejścia na konto aplikacji."""
    snapshot.config.ms_password = SecretStr("")
    with pytest.raises(ValueError, match="Niepełne dane"):
        snapshot.load()
    assert not snapshot.connections


def test_historical_issue_keeps_old_contract_and_customer(snapshot):
    """Zmiana klienta nie przypisuje starego rozchodu do dzisiejszej umowy."""
    for table in ("ZLECENIE", "ZPOZYCJA", "ZAKUPY", "ZAKPOZYCJA"):
        snapshot.tables[table][0]["ID_KLIENT"] = 41
    snapshot.tables["ZLECENIE"][0].update(DATA="2010-01-10", ROK=2010)
    snapshot.tables["ZPOZYCJA"][0]["ROK"] = 2010
    snapshot.tables["ZAKUPY"][0].update(
        DATA_WYST="2010-01-10", DATA_PRZY_WYDA="2010-01-10", ROK_ZLECENIA=2010
    )
    data = fact(snapshot.load(), "ZAKPOZYCJA:321")["data"]
    assert data["status"] == "confirmed"
    assert data["contract_id"] == 7
    assert data["customer_id"] == 41
    assert data["purchase_value"] == "240.0000"


def test_paid_service_does_not_become_contract_cost(snapshot):
    """Jawnie płatne zlecenie nie staje się kosztem umowy tylko przez aktywną kartotekę."""
    snapshot.tables["ZLECENIE"][0]["RODZAJ_US"] = "Płatne"
    data = fact(snapshot.load(), "ZAKPOZYCJA:321")["data"]
    assert data["is_contract"] is False
    assert "purchase_cost" not in data
    assert "purchase_value" not in data


def test_duplicate_parts_are_preserved_and_block_cost(snapshot):
    """Brak klucza konkretnej pozycji między tabelami nie jest zastępowany pierwszą częścią."""
    snapshot.tables["ZPOZYCJA"].append(dict(snapshot.tables["ZPOZYCJA"][0], ID_ZPOZYCJA_TABLE=71))
    data = fact(snapshot.load(), "ZAKPOZYCJA:321")["data"]
    assert data["status"] == "conflict"
    assert len(data["raw"]["ZPOZYCJA"]) == 2
    assert data["purchase_value"] is None


def test_orphan_part_and_invalid_cpc_period_remain_visible(snapshot):
    """Niepełny rok zlecenia i błędny okres CPC nie usuwają oryginalnych rekordów."""
    snapshot.tables["ZPOZYCJA"][0]["ROK"] = None
    snapshot.tables["CPC"][0]["MIESIAC"] = 13
    result = snapshot.load()
    assert fact(result, "ZPOZYCJA:70")["data"]["raw"]["ZPOZYCJA"][0]["ROK"] is None
    assert fact(result, "ZPOZYCJA:70")["status"] == "missing"
    assert fact(result, "CPC:1")["status"] == "missing"
    assert fact(result, "CPC:1")["observed_at"] is None


def test_invoice_queries_use_collected_keys_not_correlated_full_scans(snapshot):
    """Chroni poprawkę rzeczywistego limitu czasu pilota przy dużych tabelach faktur."""
    snapshot.load(only_machine_ids=[17])
    invoice_queries = [sql for sql, _ in snapshot.cursors[0].queries if "FROM FAKTURA t" in sql]
    assert invoice_queries
    assert all("EXISTS" not in sql for sql in invoice_queries)
    assert any("t.ID_FAKTURA_TABLE IN" in sql for sql in invoice_queries)


def test_missing_document_date_blocks_otherwise_agreed_cost(snapshot):
    """Brak daty dokumentu nie jest zastępowany czasem synchronizacji."""
    snapshot.tables["ZAKUPY"][0].update(DATA_WYST=None, DATA_PRZY_WYDA=None)
    item = fact(snapshot.load(), "ZAKPOZYCJA:321")
    assert item["status"] == "missing"
    assert item["observed_at"] is None
    assert item["data"]["purchase_value"] is None


def test_known_machine_without_active_contract_is_loaded_in_pilot(snapshot):
    """Ścisły pilot obejmuje również wskazaną maszynę po zawieszeniu umowy."""
    snapshot.tables["MASZYNA"][0]["ID_UMOWACPC"] = None
    result = snapshot.load(only_machine_ids=[17])
    assert [device["status"] for device in result["devices"]] == ["suspended"]
    assert fact(result, "CPC:1")["device_id"] == "ms:17"


def test_missing_current_contract_requires_review_not_suspension(snapshot):
    """Nieistniejący rekord wskazanej umowy nie oznacza zakończenia jej obowiązywania."""
    snapshot.tables["MASZYNA"][0]["ID_UMOWACPC"] = 777
    device = snapshot.load(only_machine_ids=[17])["devices"][0]
    assert device["status"] == "review"
    assert device["contract_id"] == 777
    assert "missing_current_contract" in device["data"]["issues"]


def test_legacy_document_lines_use_verified_number_company_relation(snapshot):
    """Starsze linie z pustym ID_ZAKUPY łączy klucz potwierdzony triggerem MS."""
    snapshot.tables["ZAKPOZYCJA"][0].update(
        ID_ZAKUPY=None, ID_FIRMA=1, NUMER="RW / 12 / 2026", DATA_PRZY_WYDA="2026-09-10"
    )
    data = fact(snapshot.load(), "ZAKPOZYCJA:321")["data"]
    assert data["status"] == "confirmed"
    assert data["document_link_basis"] == "NUMER+ID_FIRMA"
    assert data["issue_purchase_cost"]["status"] == "known"
    assert data["issue_purchase_cost"]["amount"] == "240.0000"


def test_gross_issue_value_does_not_require_invented_returns_or_currency(snapshot):
    """Znane wydanie pozostaje oddzielone od nieznanej waluty i bilansu po zwrotach."""
    snapshot.tables["ZPOZYCJA"][0]["POBRANO"] = None
    data = fact(snapshot.load(), "ZAKPOZYCJA:321")["data"]
    assert data["status"] == "confirmed"
    assert data["role"] == "contract"
    assert data["issued_quantity_basis"] == "ZAKPOZYCJA.POBRANO"
    assert data["returned_quantity"] is None
    assert data["returned_quantity_basis"] is None
    assert data["quantity_reconciliation"]["status"] == "unknown"
    cost = data["issue_purchase_cost"]
    assert cost["status"] == "known"
    assert cost["amount"] == "240.0000"
    assert cost["scope"] == "before_returns"
    assert cost["returns_status"] == cost["currency_status"] == "unverified"
    assert cost["currency"] is None
    assert cost["currency_scope"] == "ms:company:1"


@pytest.mark.parametrize("duplicate_company,expected", [(1, "conflict"), (2, "confirmed")])
def test_legacy_document_number_requires_unique_company_scope(
    snapshot, duplicate_company, expected
):
    """Powielony numer w firmie blokuje przypisanie; inna firma nie miesza dokumentów."""
    snapshot.tables["ZAKPOZYCJA"][0].update(ID_ZAKUPY=None, ID_FIRMA=1, NUMER="RW / 12 / 2026")
    snapshot.tables["ZAKUPY"].append(
        dict(
            snapshot.tables["ZAKUPY"][0],
            ID_ZAKUPY_TABLE=301,
            ID_FIRMA=duplicate_company,
            ID_ZLECENIE=999,
        )
    )
    result = snapshot.load(only_machine_ids=[17])
    key = "ZAKPOZYCJA:321:ZAKUPY:300" if expected == "conflict" else "ZAKPOZYCJA:321"
    data = fact(result, key)["data"]
    assert data["status"] == expected
    assert data["issue_purchase_cost"]["status"] == (
        "conflict" if expected == "conflict" else "known"
    )


def test_conflicting_explicit_and_legacy_keys_block_gross_issue_value(snapshot):
    """Numer nie nadpisuje sprzecznego dodatniego ID dokumentu."""
    snapshot.tables["ZAKPOZYCJA"][0].update(ID_ZAKUPY=301, ID_FIRMA=1, NUMER="RW / 12 / 2026")
    snapshot.tables["ZAKUPY"].append(
        dict(
            snapshot.tables["ZAKUPY"][0],
            ID_ZAKUPY_TABLE=301,
            NUMER="RW / 13 / 2026",
            ID_ZLECENIE=999,
        )
    )
    data = fact(snapshot.load(), "ZAKPOZYCJA:321:ZAKUPY:300")["data"]
    assert data["status"] == "conflict"
    assert data["issue_purchase_cost"]["amount"] is None
    assert "conflicting_document_line_keys" in data["issues"]


def test_missing_document_purchase_price_does_not_become_known_gross_cost(snapshot):
    """Rozdzielenie bilansu zwrotów nie osłabia wymagania uzgodnienia historycznych cen."""
    snapshot.tables["ZAKPOZYCJA"][0]["CENA_Z"] = None
    data = fact(snapshot.load(), "ZAKPOZYCJA:321")["data"]
    assert data["issue_purchase_cost"]["status"] == "unknown"
    assert data["issue_purchase_cost"]["amount"] is None
    assert "missing_purchase_evidence" in data["issue_purchase_cost"]["issues"]
