"""Testy okresów rozliczeniowych MS bez dostępu do źródeł produkcyjnych."""

from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, func, select

from app.models import telemetry as tables
from app.services.telemetry import sources
from app.services.telemetry.billing import cpc_reading
from app.services.telemetry.config import TelemetrySettings
from app.services.telemetry.runner import ImportRunner
from app.services.telemetry.store import TelemetryStore


def cpc_row(**values):
    """Tworzy syntetyczny okres, w którym brak koloru nie oznacza zera."""
    return {
        "ID_CPC_TABLE": 1,
        "ID_MASZYNA": 12,
        "ID_KLIENT": 20,
        "ID_UMOWACPC": 30,
        "ID_FAKTURA": 40,
        "ROK": 2026,
        "MIESIAC": 8,
        "LICZNIK_MONO_START": 100,
        "LICZNIK_MONO_END": 150,
        "LICZNIK_KOLOR_END": None,
        "LICZNIK_MONOA3_END": 7,
        **values,
    }


@pytest.fixture
def engine():
    """Ogranicza bazę testu do siedmiu tabel telemetrycznych SQLite."""
    database = create_engine(
        "sqlite://", execution_options={"schema_translate_map": {"ctip": None}}
    )
    tables.Base.metadata.create_all(database, tables=list(tables.TELEMETRY_TABLES))
    yield database
    database.dispose()


def save(engine, row):
    """Zapisuje wyłącznie syntetyczny okres CPC."""
    with engine.begin() as connection:
        store = TelemetryStore(connection)
        source = store.ensure_source("ms_cpc", "database")
        return store.ingest(
            source["id"], f"CPC/{row['ID_CPC_TABLE']}", [cpc_reading(row, {12: "TEST-CPC"})]
        )


def test_period_is_not_fabricated_measurement_date():
    """Miesiąc i faktura nie nadają licznikom fikcyjnej daty dziennej."""
    reading = cpc_reading(cpc_row(), {12: "TEST"})
    assert reading.observed_at is None
    assert reading.precision == "month"
    assert reading.payload["__ctip_billing__"] == {
        "start": "2026-08-01",
        "end": "2026-08-31",
        "invoice_linked": True,
    }
    assert reading.measurements["billing.end.black"] == 150
    assert reading.measurements["billing.end.black_a3"] == 7
    assert "billing.end.color" not in reading.measurements
    assert not any(key.startswith("lifetime.") for key in reading.measurements)


@pytest.mark.parametrize("year,month", [(1218, 1), (2026, 0), (2026, 13), (None, 1)])
def test_invalid_period_is_retained_without_normalization(year, month):
    """Nie zamienia miesiąca zero ani błędnego roku na pozornie poprawny okres."""
    reading = cpc_reading(cpc_row(ROK=year, MIESIAC=month), {})
    assert reading.payload["ROK"] == year
    assert reading.payload["MIESIAC"] == month
    assert reading.payload["__ctip_billing__"]["start"] is None
    assert ("invalid_billing_period", "") in reading.issues


def test_uninvoiced_and_decreasing_counters_are_quality_flags():
    """Zachowuje szkic z niższym licznikiem bez poprawiania źródła."""
    reading = cpc_reading(cpc_row(ID_FAKTURA=None, LICZNIK_MONO_END=90), {})
    assert ("billing_uninvoiced", "") in reading.issues
    assert ("billing_counter_decrease", "black") in reading.issues


def test_revision_and_current_customer_do_not_rewrite_historical_binding(engine):
    """Ponowienie jest idempotentne, korekta wersjonowana, a dawny klient nie jest nadpisywany."""
    assert save(engine, cpc_row())["new"] == 1
    assert save(engine, cpc_row())["new"] == 0
    assert save(engine, cpc_row(LICZNIK_MONO_END=151))["new"] == 1
    with engine.begin() as connection:
        source = TelemetryStore(connection).ensure_source("ms_cpc", "database")
        TelemetryStore(connection).bind_devices(source["id"], {"TEST-CPC": [(12, 999)]})
        link = connection.execute(select(tables.device_link)).mappings().one()
        assert link["ms_customer_id"] == 20
        assert link["status"] == "source_confirmed"
        assert connection.execute(select(func.count()).select_from(tables.record)).scalar_one() == 2


def test_late_billing_history_detects_regression_but_skips_drafts(engine):
    """Sprawdza również spadek wykryty po późniejszym dodaniu starszego miesiąca."""
    save(engine, cpc_row(ID_CPC_TABLE=2, LICZNIK_MONO_START=0, LICZNIK_MONO_END=100))
    save(engine, cpc_row(MIESIAC=7, LICZNIK_MONO_START=0, LICZNIK_MONO_END=200))
    save(engine, cpc_row(ID_CPC_TABLE=3, MIESIAC=9, LICZNIK_MONO_END=0, ID_FAKTURA=None))
    with engine.connect() as connection:
        assert (
            connection.execute(
                select(func.count())
                .select_from(tables.issue)
                .where(tables.issue.c.code == "billing_period_decrease")
            ).scalar_one()
            == 1
        )


def test_ms_reader_limits_period_and_joins_correct_active_contract_key(monkeypatch):
    """Pilnuje odrębnych kluczy maszyny i umowy oraz trzyletniego okna miesięcznego."""
    queries = []

    class Cursor:
        description = [("ID_CPC_TABLE",)]

        def execute(self, query, parameters=None):
            queries.append((query, parameters))

        def fetchall(self):
            return [(1,)]

    @contextmanager
    def connection(**kwargs):
        class Connection:
            def cursor(self):
                return Cursor()

        yield Connection()

    monkeypatch.setattr(sources, "firebird_connection", connection)
    config = TelemetrySettings(_env_file=None, TELEMETRY_HISTORY_YEARS=3)
    assert sources.ms_cpc_page(50, 20, config) == [{"ID_CPC_TABLE": 1}]
    query, parameters = queries[0]
    assert "u.ID_UMOWACPC_TABLE=m.ID_UMOWACPC" in query
    assert "m.ID_MASZYNA=c.ID_MASZYNA" in query
    assert "u.AKTYWNA='TAK'" in query
    assert "m.ID_MASZYNA_TABLE" not in query
    assert parameters[0] == 50
    assert parameters[1] == datetime.now(UTC).year - 3


def test_failed_cpc_write_keeps_checkpoint_and_can_resume(engine, monkeypatch):
    """Nie traci porcji CPC przy nieudanym zapisie transakcji docelowej."""
    monkeypatch.setattr(sources, "ms_cpc_serials", lambda config: {12: "TEST-CPC"})
    monkeypatch.setattr(sources, "ms_cpc_page", lambda after, limit, config: [cpc_row()])
    config = TelemetrySettings(_env_file=None, TELEMETRY_MS_CPC_ENABLED=True, TELEMETRY_PAGE_SIZE=2)
    runner = ImportRunner(config, engine)
    with monkeypatch.context() as scoped:

        def fail(*args, **kwargs):
            raise RuntimeError("przerwanie zapisu")

        scoped.setattr(TelemetryStore, "ingest", fail)
        runner.run_database("ms_cpc")
    with engine.connect() as connection:
        assert connection.execute(select(tables.source.c.checkpoint)).scalar_one() == {}
    ImportRunner(config, engine).run_database("ms_cpc")
    with engine.connect() as connection:
        assert connection.execute(select(func.count()).select_from(tables.record)).scalar_one() == 1
