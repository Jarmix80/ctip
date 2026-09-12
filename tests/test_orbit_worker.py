"""Testy workera ORBIT z atrapami źródeł i transakcjami SQLite w pamięci."""

import logging
from contextlib import nullcontext
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from sqlalchemy import create_engine, event, select, update

from app import orbit_worker as worker
from app.services import orbit, orbit_source

NOW = datetime(2026, 9, 12, 20, tzinfo=UTC)
SLOT = "2026-09-11"
NEXT_SLOT = "2026-09-12"


class StopLoop(BaseException):
    """Kończy wyłącznie pętlę testową, bez rzeczywistego oczekiwania."""


def snapshot(identifiers=(7, 8)):
    """Tworzy pełny odczyt syntetycznych maszyn w podanym zakresie."""
    return {
        "complete": True,
        "devices": [{"id": f"ms:{identifier}"} for identifier in identifiers],
        "facts": [{"device_id": f"ms:{identifier}"} for identifier in identifiers],
    }


@pytest.fixture
def runtime(monkeypatch):
    """Izoluje źródło MS, silnik PG, pliki logów i czas harmonogramu."""
    engine = SimpleNamespace(
        connect=Mock(side_effect=AssertionError("Nieoczekiwany odczyt PostgreSQL")),
        begin=Mock(side_effect=AssertionError("Nieoczekiwany zapis PostgreSQL")),
        dispose=Mock(),
    )
    directory = MagicMock()
    directory.resolve.return_value = directory
    directory.parents = [directory, directory]
    directory.__truediv__.return_value = directory
    state = SimpleNamespace(
        engine=engine,
        slot=SLOT,
        directory=directory,
        daily_log=Mock(return_value=logging.NullHandler()),
        validate=Mock(),
        lock=Mock(side_effect=lambda _engine: nullcontext(True)),
        create_engine=Mock(return_value=engine),
        snapshot=Mock(
            side_effect=lambda machine_ids=None, only_machine_ids=None: snapshot(
                (7, 8) if only_machine_ids is None else only_machine_ids
            )
        ),
        ingest=Mock(side_effect=AssertionError("Nieoczekiwany zapis odczytu MS")),
        shipments=Mock(side_effect=AssertionError("Nieoczekiwany import przesyłek")),
        project=Mock(side_effect=AssertionError("Nieoczekiwana zmiana projekcji")),
        put=Mock(wraps=orbit.put),
        sleep=Mock(side_effect=StopLoop),
    )
    for name in ("validate", "create_engine"):
        monkeypatch.setattr(worker, name, getattr(state, name))
    monkeypatch.setattr(worker, "worker_lock", state.lock)
    monkeypatch.setattr(worker, "scheduled_slot", lambda: state.slot)
    monkeypatch.setattr(worker, "DailyLog", state.daily_log)
    monkeypatch.setattr(worker, "Path", Mock(return_value=directory))
    monkeypatch.setattr(
        worker, "settings", SimpleNamespace(database_url="postgresql://localhost/ctip_test")
    )
    monkeypatch.setattr(worker.logging, "basicConfig", Mock())
    monkeypatch.setattr(worker.time, "sleep", state.sleep)
    monkeypatch.setattr(orbit_source, "load_orbit_snapshot", state.snapshot)
    monkeypatch.setattr(orbit, "ingest_snapshot", state.ingest)
    monkeypatch.setattr(orbit, "ingest_shipments", state.shipments)
    monkeypatch.setattr(orbit, "project", state.project)
    monkeypatch.setattr(orbit, "put", state.put)
    monkeypatch.setattr(orbit, "utcnow", lambda: NOW)
    return state


@pytest.fixture
def database(runtime):
    """Udostępnia dwa gotowe raporty i rejestr zadań, bez Alembic i bazy sieciowej."""
    engine = create_engine("sqlite://", execution_options={"schema_translate_map": {"ctip": None}})

    @event.listens_for(engine, "connect")
    def configure_transactions(connection, _record):
        """Wyłącza odroczony BEGIN SQLite, aby savepoint nie zastępował transakcji nadrzędnej."""
        connection.isolation_level = None

    @event.listens_for(engine, "begin")
    def begin_transaction(connection):
        """Otwiera transakcję przed pierwszym savepointem, tak jak PostgreSQL."""
        connection.exec_driver_sql("BEGIN")

    orbit.DEVICES.metadata.create_all(engine, tables=[orbit.DEVICES, orbit.RUNS])
    with engine.begin() as connection:
        for identifier in (7, 8):
            connection.execute(
                orbit.DEVICES.insert().values(
                    id=f"ms:{identifier}",
                    ms_machine_id=identifier,
                    serial=f"TEST-{identifier}",
                    model="Model testowy",
                    customer="Klient testowy",
                    status="active",
                    data={"version": "previous"},
                    report={"version": "previous"},
                    fingerprint="previous",
                    synced_at=NOW,
                    updated_at=NOW,
                )
            )

    def ingest(connection, incoming):
        """Symuluje zapis danych MS; rejestr przebiegów aktualizuje wyłącznie worker."""
        selected = [device["id"] for device in incoming["devices"]]
        connection.execute(
            update(orbit.DEVICES)
            .where(orbit.DEVICES.c.id.in_(selected))
            .values(data={"version": "incoming"})
        )
        return {"devices": len(selected), "facts": len(incoming["facts"])}

    def shipments(connection, *, device_ids=None):
        """Zachowuje sygnaturę importera przesyłek bez odczytu dokumentów Shipping."""
        return {"shipments": 0}

    def project(connection, device_ids=None):
        """Zastępuje raporty w faktycznej transakcji, respektując także pusty zakres."""
        statement = update(orbit.DEVICES).values(report={"version": "current"})
        if device_ids is not None:
            statement = statement.where(orbit.DEVICES.c.id.in_(device_ids))
        result = connection.execute(statement)
        return {"updated": result.rowcount}

    runtime.engine.connect = Mock(wraps=engine.connect)
    runtime.engine.begin = Mock(wraps=engine.begin)
    runtime.ingest.side_effect = ingest
    runtime.shipments.side_effect = shipments
    runtime.project.side_effect = project
    yield engine
    engine.dispose()


def rows(database, table):
    """Odczytuje wyłącznie nietrwały stan testowy w stabilnej kolejności PK."""
    with database.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                select(table).order_by(*table.primary_key.columns)
            ).mappings()
        ]


def ms_run(database):
    """Pobiera znacznik ostatniego importu MS, jeśli już istnieje."""
    return next((row for row in rows(database, orbit.RUNS) if row["name"] == "ms"), None)


def completed_slot(database, *, slot=SLOT, status="ok"):
    """Zapisuje wcześniejsze wykonanie pełnego przebiegu w bazie testowej."""
    with database.begin() as connection:
        orbit.put(
            connection,
            orbit.RUNS,
            {"name": "ms"},
            dict(
                finished_at=NOW,
                status=status,
                counts={"slot": slot, "devices": 2, "facts": 2},
                error_code=None,
            ),
        )


@pytest.mark.parametrize("machine_ids", [None, [7]])
def test_dry_run_cycle_never_reads_or_writes_postgres(runtime, machine_ids):
    """Kontrola źródła działa bez docelowych tabel, blokady PG i jakiegokolwiek zapisu."""
    result = worker.cycle(runtime.engine, dry_run=True, machine_ids=machine_ids)
    count = 2 if machine_ids is None else 1
    assert result == {"status": "dry_run", "devices": count, "facts": count}
    runtime.snapshot.assert_called_once_with(only_machine_ids=machine_ids)
    for dependency in (
        runtime.engine.connect,
        runtime.engine.begin,
        runtime.lock,
        runtime.ingest,
        runtime.shipments,
        runtime.project,
        runtime.put,
    ):
        dependency.assert_not_called()


@pytest.mark.parametrize("failure", [False, True])
def test_dry_run_cli_has_no_database_or_file_writes(runtime, caplog, failure):
    """Sukces i awaria kontroli nie zapisują nawet statusu błędu ani logu dziennego."""
    if failure:
        runtime.snapshot.side_effect = RuntimeError("PRYWATNA_TRESC_ZRODLA")
    with caplog.at_level(logging.INFO):
        result = worker.main(["--once", "--import-ms", "--dry-run", "--machine-id", "7"])
    assert result == (1 if failure else 0)
    runtime.snapshot.assert_called_once_with(only_machine_ids=[7])
    runtime.engine.connect.assert_not_called()
    runtime.engine.begin.assert_not_called()
    runtime.put.assert_not_called()
    runtime.daily_log.assert_not_called()
    runtime.directory.mkdir.assert_not_called()
    assert "PRYWATNA_TRESC_ZRODLA" not in caplog.text


@pytest.mark.parametrize(
    "arguments",
    [
        ["--loop", "--dry-run"],
        ["--loop", "--import-ms", "--machine-id", "7"],
        ["--once", "--machine-id", "7"],
    ],
)
def test_invalid_cli_modes_stop_before_source_or_engine(runtime, arguments):
    """Niedozwolone połączenia trybów nie rozpoczynają pracy na danych."""
    with pytest.raises(SystemExit) as error:
        worker.main(arguments)
    assert error.value.code == 2
    runtime.validate.assert_not_called()
    runtime.create_engine.assert_not_called()
    runtime.snapshot.assert_not_called()


def test_busy_lock_skips_source_and_projection(runtime):
    """Zajęta blokada kończy cykl przed odczytem floty i transakcją projekcji."""
    runtime.lock.side_effect = lambda _engine: nullcontext(False)
    assert worker.cycle(runtime.engine, import_ms=True) == {"status": "already_running"}
    runtime.engine.connect.assert_not_called()
    runtime.engine.begin.assert_not_called()
    runtime.snapshot.assert_not_called()
    runtime.project.assert_not_called()


def test_projection_only_does_not_read_ms_even_without_checkpoint(runtime, database):
    """Pięciominutowa pętla nie konkuruje z nocnym właścicielem importu MS."""
    worker.cycle(runtime.engine, project_only=True)
    runtime.snapshot.assert_not_called()
    runtime.project.assert_called_once()


def test_busy_once_returns_retry_code(runtime):
    """Kolizja projekcji nie może zatwierdzić pominiętego importu nocnego."""
    runtime.lock.side_effect = lambda _engine: nullcontext(False)
    assert worker.main(["--once"]) == 3


@pytest.mark.parametrize(
    "status,slot,forced,expected_imports",
    [
        ("ok", SLOT, False, 0),
        ("ok", SLOT, True, 1),
        ("error", SLOT, False, 1),
        ("ok", "2026-09-10", False, 1),
    ],
)
def test_cycle_imports_only_when_due(runtime, database, status, slot, forced, expected_imports):
    """Wykonany slot pomija MS; wymuszenie, zaległość i błąd uruchamiają ponowienie."""
    completed_slot(database, slot=slot, status=status)
    assert worker.cycle(runtime.engine, import_ms=forced) == {"status": "ok", "updated": 2}
    assert runtime.snapshot.call_count == expected_imports
    if expected_imports:
        runtime.snapshot.assert_called_once_with(machine_ids=[7, 8], only_machine_ids=None)
    runtime.project.assert_called_once()


def test_loop_imports_once_per_slot_and_projects_every_five_minutes(runtime, database):
    """Wymuszenie dotyczy pierwszego cyklu; nowy slot dopuszcza dokładnie jeden import."""
    completed_slot(database)
    sleeps = []

    def advance_clock(delay):
        """Przesuwa zegar po dwóch projekcjach, a po czterech kończy pętlę."""
        sleeps.append(delay)
        if len(sleeps) == 2:
            runtime.slot = NEXT_SLOT
        if len(sleeps) == 4:
            raise StopLoop

    runtime.sleep.side_effect = advance_clock
    with pytest.raises(StopLoop):
        worker.main(["--loop", "--import-ms"])
    assert sleeps == [300, 300, 300, 300]
    assert runtime.snapshot.call_count == 2
    assert runtime.project.call_count == 4
    assert ms_run(database)["counts"]["slot"] == NEXT_SLOT
    runtime.engine.dispose.assert_called_once()


def test_import_spanning_slot_boundary_does_not_complete_next_slot(runtime, database):
    """Import rozpoczęty przed 23:55 nie może zaliczyć nowego slotu po zakończeniu."""

    def delayed_snapshot(**_kwargs):
        """Symuluje przekroczenie 23:55 podczas pobierania historii."""
        runtime.slot = NEXT_SLOT
        return snapshot()

    runtime.snapshot.side_effect = delayed_snapshot
    worker.cycle(runtime.engine)
    assert ms_run(database)["counts"]["slot"] == SLOT
    worker.cycle(runtime.engine)
    assert runtime.snapshot.call_count == 2
    assert ms_run(database)["counts"]["slot"] == NEXT_SLOT


@pytest.mark.parametrize("machine_ids", [[7], []])
def test_pilot_limits_snapshot_and_projection_including_empty_scope(runtime, database, machine_ids):
    """Lista pilota, również pusta, nigdy nie rozszerza projekcji na pozostałą flotę."""
    before = rows(database, orbit.DEVICES)
    worker.cycle(runtime.engine, import_ms=True, machine_ids=machine_ids)
    runtime.snapshot.assert_called_once_with(machine_ids=[7, 8], only_machine_ids=machine_ids)
    selected = [f"ms:{identifier}" for identifier in machine_ids]
    assert runtime.project.call_args.kwargs["device_ids"] == selected
    assert runtime.shipments.call_args.kwargs["device_ids"] == selected
    assert [row for row in rows(database, orbit.DEVICES) if row["id"] not in selected] == [
        row for row in before if row["id"] not in selected
    ]


def test_pilot_does_not_import_shipments_for_whole_fleet(runtime, database):
    """Pilotaż pomija globalny importer przesyłek albo przekazuje mu ten sam zakres."""
    worker.cycle(runtime.engine, import_ms=True, machine_ids=[7])
    for invocation in runtime.shipments.call_args_list:
        assert invocation.kwargs.get("device_ids") == ["ms:7"]


def test_pilot_does_not_erase_previously_completed_full_slot(runtime, database):
    """Pilotaż nie zastępuje informacji o wcześniej zakończonym pełnym imporcie."""
    completed_slot(database)
    previous = ms_run(database)
    worker.cycle(runtime.engine, import_ms=True, machine_ids=[7])
    assert ms_run(database) == previous
    pilot = next(row for row in rows(database, orbit.RUNS) if row["name"] == "ms_pilot")
    assert pilot["status"] == "ok"
    assert pilot["counts"]["devices"] == 1
    assert pilot["counts"].get("slot") != SLOT


def test_pilot_does_not_complete_a_full_slot_and_full_import_still_runs(runtime, database):
    """Po pierwszym pilotażu pełna flota nadal wymaga swojego nocnego importu."""
    worker.cycle(runtime.engine, import_ms=True, machine_ids=[7])
    previous = ms_run(database)
    assert previous is None or previous["counts"].get("slot") != SLOT
    worker.cycle(runtime.engine)
    assert runtime.snapshot.call_count == 2
    assert ms_run(database)["counts"]["slot"] == SLOT


@pytest.mark.parametrize("stage", ["shipments", "project", "put"])
def test_failure_rolls_back_projection_and_completed_slot(runtime, database, stage):
    """Awaria części PG wycofuje raporty, dane urządzeń i slot MS w transakcji nadrzędnej."""
    completed_slot(database, slot="2026-09-10")
    before = {table.name: rows(database, table) for table in (orbit.DEVICES, orbit.RUNS)}
    operation = getattr(runtime, stage)
    original = operation.side_effect or operation._mock_wraps

    def fail_after_write(*args, **kwargs):
        """Przerywa etap także po częściowym zapisie do transakcji."""
        if original is not None:
            original(*args, **kwargs)
        if stage == "put" and args[2] != {"name": "worker"}:
            return
        raise RuntimeError("przerwanie etapu")

    operation.side_effect = fail_after_write
    with pytest.raises(RuntimeError, match="przerwanie etapu"):
        worker.cycle(runtime.engine)
    assert {table.name: rows(database, table) for table in (orbit.DEVICES, orbit.RUNS)} == before


@pytest.mark.parametrize("stage,error_type", [("snapshot", TimeoutError), ("ingest", ValueError)])
def test_ms_failure_preserves_source_data_and_continues_pg_projection(
    runtime, database, caplog, stage, error_type
):
    """Błąd odczytu lub walidacji MS zachowuje dane źródła i kończy projekcję jako partial."""
    completed_slot(database, slot="2026-09-10")
    previous = ms_run(database)
    original = runtime.ingest.side_effect

    def fail_ms(*args, **kwargs):
        """Walidacja może zawieść po zapisie części MS do wycofywanego savepointu."""
        if stage == "ingest":
            original(*args, **kwargs)
        raise error_type("PRYWATNA_TRESC_ZRODLA")

    getattr(runtime, stage).side_effect = fail_ms
    with caplog.at_level(logging.INFO):
        result = worker.cycle(runtime.engine)
    assert result == {"status": "partial", "updated": 2}
    assert all(row["data"] == {"version": "previous"} for row in rows(database, orbit.DEVICES))
    assert all(row["report"] == {"version": "current"} for row in rows(database, orbit.DEVICES))
    assert ms_run(database)["status"] == "error"
    assert ms_run(database)["error_code"] == error_type.__name__
    assert ms_run(database)["counts"] == previous["counts"]
    status = next(row for row in rows(database, orbit.RUNS) if row["name"] == "worker")
    assert status["status"] == "partial"
    assert status["error_code"] == error_type.__name__
    runtime.shipments.assert_called_once()
    runtime.project.assert_called_once()
    assert "PRYWATNA_TRESC_ZRODLA" not in caplog.text


def test_cli_partial_ms_failure_returns_exit_one(runtime, database):
    """Jednorazowy przebieg partial zwraca 1 mimo poprawnej aktualizacji projekcji PG."""
    runtime.snapshot.side_effect = TimeoutError("awaria MS")
    assert worker.main(["--once", "--import-ms"]) == 1
    runtime.project.assert_called_once()
    assert ms_run(database)["status"] == "error"
    status = next(row for row in rows(database, orbit.RUNS) if row["name"] == "worker")
    assert status["status"] == "partial"
    assert status["error_code"] == "TimeoutError"
    runtime.engine.dispose.assert_called_once()


def test_cli_failure_preserves_projection_and_records_only_safe_error(runtime, database, caplog):
    """Po rollbacku main zapisuje osobno bezpieczny status, zachowując gotowe raporty."""
    before = rows(database, orbit.DEVICES)
    original = runtime.project.side_effect

    def fail_after_projection(*args, **kwargs):
        """Wywołuje błąd po zapisaniu nowego raportu przed zatwierdzeniem."""
        original(*args, **kwargs)
        raise RuntimeError("PRYWATNA_TRESC_ZRODLA")

    runtime.project.side_effect = fail_after_projection
    with caplog.at_level(logging.INFO):
        assert worker.main(["--once", "--import-ms"]) == 1
    assert rows(database, orbit.DEVICES) == before
    assert ms_run(database) is None
    failures = rows(database, orbit.RUNS)
    assert len(failures) == 1
    assert failures[0]["name"] == "worker"
    assert failures[0]["status"] == "error"
    assert failures[0]["error_code"] == "RuntimeError"
    assert "PRYWATNA_TRESC_ZRODLA" not in caplog.text
    runtime.engine.dispose.assert_called_once()


def test_loop_retries_failed_snapshot_without_completing_slot(runtime, database):
    """Awaria MS dopuszcza projekcję PG i ponowienie źródła w tym samym slocie."""
    runtime.snapshot.side_effect = [RuntimeError("awaria źródła"), snapshot()]
    sleeps = []

    def retry(delay):
        """Sprawdza stan po awarii i kończy pętlę po skutecznym ponowieniu."""
        sleeps.append(delay)
        if len(sleeps) == 1:
            assert all(
                row["data"] == {"version": "previous"} for row in rows(database, orbit.DEVICES)
            )
            assert all(
                row["report"] == {"version": "current"} for row in rows(database, orbit.DEVICES)
            )
            assert ms_run(database)["status"] == "error"
            assert ms_run(database)["counts"].get("slot") != SLOT
        else:
            raise StopLoop

    runtime.sleep.side_effect = retry
    with pytest.raises(StopLoop):
        worker.main(["--loop", "--import-ms"])
    assert sleeps == [300, 300]
    assert runtime.snapshot.call_count == 2
    assert runtime.project.call_count == 2
    assert ms_run(database)["counts"]["slot"] == SLOT
