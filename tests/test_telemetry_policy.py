"""Testy polityk CSV, poczty i migawek na izolowanych danych syntetycznych."""

import gzip
import hashlib
from contextlib import contextmanager
from datetime import UTC, date, datetime
from email.message import EmailMessage

import pytest
from sqlalchemy import create_engine, func, select

from app.models import telemetry as tables
from app.services.telemetry import daily, sources
from app.services.telemetry.config import TelemetrySettings
from app.services.telemetry.mail_import import move_delivery
from app.services.telemetry.mail_parsing import parse_remote_message
from app.services.telemetry.parsers import parse_csv
from app.services.telemetry.runner import ImportRunner
from app.services.telemetry.store import TelemetryStore
from app.telemetry_worker import scheduled_slot


@pytest.fixture
def engine():
    """Tworzy wyłącznie nietrwałe tabele telemetrii."""
    database = create_engine(
        "sqlite://", execution_options={"schema_translate_map": {"ctip": None}}
    )
    tables.Base.metadata.create_all(database, tables=list(tables.TELEMETRY_TABLES))
    yield database
    database.dispose()


def config(**values):
    """Nie odczytuje lokalnych poświadczeń ani konfiguracji produkcyjnej."""
    return TelemetrySettings(_env_file=None, TELEMETRY_ENABLED=True, **values)


def scalar(engine, table):
    """Zlicza wyniki bez odczytywania treści źródłowej."""
    with engine.connect() as connection:
        return connection.execute(select(func.count()).select_from(table)).scalar_one()


def csv_blob():
    """Zwraca pojedynczy anonimowy pomiar Remote."""
    return b"Device Serial Number,Acquisition Date (mm/dd/yyyy),Acquisition Time,Total\nTEST-1,08/01/2026,10:00,123\n"


def message(serial="TEST-1"):
    """Buduje syntetyczne zgłoszenie z licznikiem."""
    result = EmailMessage()
    result["Subject"] = f"Toner Supply Call, Device S/N: {serial}"
    result["Message-ID"] = f"<{serial}@test.invalid>"
    result["Date"] = "Fri, 11 Sep 2026 10:00:00 +0200"
    result.set_content(f"Device S/N: {serial}\nMeter Black: 123\n")
    return result.as_bytes()


def test_all_csv_archive_and_repeated_delivery(engine, tmp_path, monkeypatch):
    """DPLAC i inne nazwy przechodzą do archiwum, bez kopiowania identycznych plików."""
    monkeypatch.setattr("app.services.telemetry.csv_import.time.sleep", lambda _: None)
    source = tmp_path / "DPLAC_test.csv"
    source.write_bytes(csv_blob())
    archive = tmp_path / "Archiwum"
    archive.mkdir()
    (archive / "old.csv").write_bytes(csv_blob())
    options = config(TELEMETRY_DPLAC_ROOT=str(tmp_path), TELEMETRY_ARCHIVE_ROOT_CSV=True)
    ImportRunner(options, engine).run()
    assert not source.exists()
    assert scalar(engine, tables.record) == 1
    source.write_bytes(csv_blob())
    ImportRunner(options, engine).run()
    assert len(list(archive.glob("*.csv"))) == 2
    assert scalar(engine, tables.artifact) == 1
    assert not (archive / "archiwum").exists()


def test_existing_dplac_marker_is_upgraded_without_new_readings(engine, tmp_path, monkeypatch):
    """Starszy znacznik bez osadzonego CSV nie blokuje nowej archiwizacji."""
    monkeypatch.setattr("app.services.telemetry.csv_import.time.sleep", lambda _: None)
    path = tmp_path / "DPLAC_old.csv"
    path.write_bytes(csv_blob())
    with engine.begin() as connection:
        store = TelemetryStore(connection)
        source = store.ensure_source("remote_dplac", "csv")
        store.ingest(source["id"], str(path), parse_csv(csv_blob()), blob=csv_blob())
    ImportRunner(
        config(TELEMETRY_DPLAC_ROOT=str(tmp_path), TELEMETRY_ARCHIVE_ROOT_CSV=True), engine
    ).run()
    assert not path.exists()
    assert scalar(engine, tables.record) == 1
    with engine.connect() as connection:
        assert (
            gzip.decompress(connection.execute(select(tables.artifact.c.content_gzip)).scalar_one())
            == csv_blob()
        )


def test_polish_csv_and_unknown_schema_are_lossless():
    """Rozdziela przyrost od licznika narastającego i zachowuje obce kolumny."""
    blob = "Numer seryjny;Data ostatniego odczytu;Licznik główny;Licznik główny (zmiana)\nTEST-1;01.08.2026 10:30;100;9\n".encode(
        "cp1250"
    )
    reading = parse_csv(blob)[0]
    assert reading.measurements == {"lifetime.total": 100}
    assert reading.payload["Licznik główny (zmiana)"] == "9"
    assert reading.observed_at == datetime(2026, 8, 1, 8, 30, tzinfo=UTC)
    unknown = parse_csv(b"Future,Value\nx,2\n")[0]
    assert unknown.kind == "unclassified_csv"
    assert unknown.payload == {"Future": "x", "Value": "2"}


class Mailbox:
    """Emuluje serwer UID MOVE bez poleceń zbiorczego usuwania."""

    def __init__(self, messages):
        self.folders = {"INBOX": dict(messages)}
        self.folder = "INBOX"
        self.generation = "100"
        self.copyuid = [None]
        self.commands = []
        self.lose_response = False

    def select(self, folder, readonly=True):
        self.folder = folder.strip('"')
        return "OK", [b"1"]

    def response(self, name):
        return name, [self.generation.encode()] if name == "UIDVALIDITY" else self.copyuid

    def capability(self):
        return "OK", [b"IMAP4rev1 UIDPLUS MOVE"]

    def list(self, reference, folder):
        return "OK", [b"exists"] if folder.strip('"') in self.folders else [None]

    def create(self, folder):
        self.folders[folder.strip('"')] = {}
        return "OK", []

    def uid(self, command, *args):
        self.commands.append(command.upper())
        messages = self.folders[self.folder]
        if command.lower() == "search":
            if "UID" in args:
                value = args[-1]
                start = int(value.split(":")[0])
                uids = (
                    [uid for uid in messages if uid >= start]
                    if ":" in value
                    else [uid for uid in messages if uid == start]
                )
            else:
                uids = list(messages)
            return "OK", [" ".join(map(str, sorted(uids))).encode()]
        if command.lower() == "fetch":
            uids = [int(value) for value in args[0].split(",")]
            if "RFC822.SIZE" in args[1]:
                return "OK", [
                    f"{uid} (UID {uid} RFC822.SIZE {len(messages[uid])})".encode()
                    for uid in uids
                    if uid in messages
                ]
            return "OK", [
                (f"{uid} (UID {uid} BODY[]".encode(), messages[uid])
                for uid in uids
                if uid in messages
            ]
        if command.upper() == "MOVE":
            uid = int(args[0])
            target = self.folders[args[1].strip('"')]
            target_uid = max(target, default=0) + 1
            target[target_uid] = messages.pop(uid)
            self.copyuid = [f"100 {uid} {target_uid}".encode()]
            if self.lose_response:
                self.lose_response = False
                raise OSError("utracona odpowiedź")
            return "OK", [b"moved"]
        raise AssertionError(command)


def install_mail(monkeypatch, mailbox, mapping=None):
    """Zastępuje wszystkie połączenia sieciowe emulatorem."""
    monkeypatch.setattr(
        sources,
        "active_identity_map",
        lambda _: mapping if mapping is not None else {"TEST-1": [(1, 2)]},
    )

    @contextmanager
    def connect(*args, **kwargs):
        yield mailbox

    monkeypatch.setattr(sources, "remote_mailbox", connect)


def test_mail_routes_only_after_commit_and_skips_inactive(engine, monkeypatch):
    """Do bazy trafia aktywne urządzenie, a na skrzynce obie wiadomości mają właściwe foldery."""
    mailbox = Mailbox({1: message(), 2: message("INACTIVE")})
    install_mail(monkeypatch, mailbox)
    options = config(TELEMETRY_MAIL_ENABLED=True, TELEMETRY_MAIL_MOVE_ENABLED=True)
    ImportRunner(options, engine, backfill=True).run()
    assert len(mailbox.folders["przetworzone"]) == 1
    assert len(mailbox.folders["odrzucone"]) == 1
    assert not mailbox.folders["INBOX"]
    assert scalar(engine, tables.mail_delivery) == 2
    assert scalar(engine, tables.record) == 1
    assert scalar(engine, tables.artifact) == 1
    ImportRunner(options, engine, backfill=True).run()
    assert scalar(engine, tables.mail_delivery) == 2
    assert "EXPUNGE" not in mailbox.commands
    assert "STORE" not in mailbox.commands


def test_lost_move_response_is_reconciled_without_duplicate(engine, monkeypatch):
    """Po zerwaniu połączenia potwierdza treść w celu, bez drugiego przeniesienia."""
    mailbox = Mailbox({1: message()})
    mailbox.lose_response = True
    install_mail(monkeypatch, mailbox)
    options = config(TELEMETRY_MAIL_ENABLED=True, TELEMETRY_MAIL_MOVE_ENABLED=True)
    first = ImportRunner(options, engine, backfill=True).run()
    assert first["remote_mail"]["errors"]
    assert len(mailbox.folders["przetworzone"]) == 1
    ImportRunner(options, engine, backfill=True).run()
    assert mailbox.commands.count("MOVE") == 1
    with engine.connect() as connection:
        assert (
            connection.execute(select(tables.mail_delivery.c.move_status)).scalar_one() == "moved"
        )


def test_mail_parse_and_ms_failure_do_not_move_messages(engine, monkeypatch):
    """Błąd XML lub niedostępny katalog nie oznacza odrzucenia urządzenia."""
    invalid = EmailMessage()
    invalid.add_attachment(b"<broken", maintype="application", subtype="xml", filename="report.xml")
    mailbox = Mailbox({1: invalid.as_bytes()})
    install_mail(monkeypatch, mailbox)
    options = config(TELEMETRY_MAIL_ENABLED=True, TELEMETRY_MAIL_MOVE_ENABLED=True)
    assert ImportRunner(options, engine).run()["remote_mail"]["errors"]
    assert 1 in mailbox.folders["INBOX"]
    assert scalar(engine, tables.mail_delivery) == 0

    def fail(_):
        raise OSError("brak źródła")

    monkeypatch.setattr(sources, "active_identity_map", fail)
    assert ImportRunner(options, engine).run()["remote_mail"]["errors"]
    assert "MOVE" not in mailbox.commands


def test_uid_generation_change_cannot_move_another_message():
    """Nie wykonuje MOVE na UID, który należy już do innej generacji."""
    mailbox = Mailbox({1: message()})
    delivery = {
        "folder": "INBOX",
        "uidvalidity": "old",
        "uid": 1,
        "sha256": hashlib.sha256(message()).hexdigest(),
        "target_folder": "przetworzone",
    }
    with pytest.raises(ValueError, match="uidvalidity_changed"):
        move_delivery(mailbox, delivery, config())
    assert not mailbox.commands


def test_multidevice_xml_and_history_keep_independent_identities():
    """Dokument zbiorczy nie przypisuje całej historii pierwszemu numerowi seryjnemu."""
    envelope = EmailMessage()
    envelope["Subject"] = "Raport"
    envelope.add_attachment(
        b"<Devices><Device><DeviceID>TEST-1</DeviceID><MeterBlack>100</MeterBlack><JamHistory><Entry><Date>2026-08-01</Date><Code>J1</Code></Entry></JamHistory></Device><Device><DeviceID>TEST-2</DeviceID><MeterBlack>200</MeterBlack></Device></Devices>",
        maintype="application",
        subtype="xml",
        filename="report.xml",
    )
    readings = parse_remote_message(envelope.as_bytes())
    assert {reading.serial for reading in readings} == {"TEST-1", "TEST-2"}
    assert any(
        reading.kind == "device_event" and reading.serial == "TEST-1" for reading in readings
    )
    assert (
        next(reading for reading in readings if reading.serial == "TEST-2").measurements[
            "lifetime.black"
        ]
        == 200
    )


def vm_row(identifier, hour, **values):
    """Tworzy wiersz historii V bez produkcyjnych numerów."""
    return {
        "ID_TBL_MASZYNY_STATS": identifier,
        "ID_MASZYNA": 7,
        "DATA": "2026-08-01",
        "CZAS": hour,
        "LICZNIK_MONO": identifier * 100,
        **values,
    }


def test_daily_chooses_last_sample_and_keeps_all_distinct_events():
    """Kolejność kluczy nie zastępuje czasu pomiaru, a historia nie znika między migawkami."""
    rows = [
        vm_row(2, "18:00", jam_history=[{"date": "2026-08-01", "code": "J1"}]),
        vm_row(1, "10:00", jam_history=[{"date": "2026-08-01", "code": "J2"}]),
        vm_row(3, "19:00", ID_MASZYNA=8),
    ]
    snapshots, events, counts = daily.prepare_page(
        "vmaintenance",
        "MASZYNY_STATS",
        rows,
        {7: "TEST-1", 8: "INACTIVE"},
        {"TEST-1": [(1, 2)]},
        date(2024, 8, 1),
        date(2026, 8, 2),
    )
    assert len(snapshots) == 1
    reading = daily.daily_reading(next(iter(snapshots.values())))
    assert reading.measurements["lifetime.black"] == 200
    assert len(events) == 2
    assert counts["excluded"] == 1


def test_daily_replay_is_idempotent_and_checkpoint_rolls_back(engine, monkeypatch):
    """Kursor dzienny nie wyprzedza danych, a ponowienie nie dodaje wersji bez zmiany."""
    monkeypatch.setattr(sources, "active_identity_map", lambda _: {"TEST-1": [(1, 2)]})
    monkeypatch.setattr(sources, "vm_serials", lambda _: {7: "TEST-1"})
    monkeypatch.setattr(
        daily,
        "vm_daily_page",
        lambda table, after, *_: (
            [vm_row(1, "10:00")] if table == "MASZYNY_STATS" and not after else []
        ),
    )
    options = config(TELEMETRY_VM_ENABLED=True)
    ImportRunner(options, engine, backfill=True).run()
    assert scalar(engine, tables.record) == 1
    ImportRunner(options, engine, backfill=True).run()
    assert scalar(engine, tables.record) == 1
    original = TelemetryStore.ingest

    def fail(store, source_id, locator, *args, **kwargs):
        if "/MASZYNY_STATS/" in locator:
            raise RuntimeError("przerwanie")
        return original(store, source_id, locator, *args, **kwargs)

    monkeypatch.setattr(TelemetryStore, "ingest", fail)
    result = ImportRunner(options, engine, backfill=True).run()
    assert result["vmaintenance"]["errors"]
    with engine.connect() as connection:
        checkpoint = connection.execute(
            select(tables.source.c.checkpoint).where(tables.source.c.name == "vmaintenance")
        ).scalar_one()
        assert not checkpoint["daily2"]["tables"].get("MASZYNY_STATS", {}).get("done")


def test_scheduled_slot_uses_polish_day_and_handles_leap_year():
    """Restart nadrabia ostatnie 23:55, nie uruchamia z wyprzedzeniem kolejnego dnia."""
    assert scheduled_slot(datetime(2026, 8, 1, 21, 54, tzinfo=UTC)) == "2026-07-31"
    assert scheduled_slot(datetime(2026, 8, 1, 21, 55, tzinfo=UTC)) == "2026-08-01"
    assert daily.years_ago(date(2024, 2, 29), 2) == date(2022, 2, 28)


def test_failed_csv_commit_never_moves_input(engine, tmp_path, monkeypatch):
    """Nie tworzy archiwalnego pliku, gdy zapis danych został wycofany."""
    monkeypatch.setattr("app.services.telemetry.csv_import.time.sleep", lambda _: None)
    path = tmp_path / "DPLAC_test.csv"
    path.write_bytes(csv_blob())

    def fail(*args, **kwargs):
        raise RuntimeError("błąd zapisu")

    monkeypatch.setattr(TelemetryStore, "ingest", fail)
    result = ImportRunner(
        config(TELEMETRY_DPLAC_ROOT=str(tmp_path), TELEMETRY_ARCHIVE_ROOT_CSV=True), engine
    ).run()
    assert result["remote_dplac"]["errors"]
    assert path.read_bytes() == csv_blob()
    assert not list((tmp_path / "Archiwum").glob("*.csv"))


def test_failed_mail_commit_never_moves_source(engine, monkeypatch):
    """MOVE nie jest wykonywany przed udanym zatwierdzeniem bazy."""
    mailbox = Mailbox({1: message()})
    install_mail(monkeypatch, mailbox)

    def fail(*args, **kwargs):
        raise RuntimeError("błąd zapisu")

    monkeypatch.setattr(TelemetryStore, "ingest", fail)
    ImportRunner(
        config(TELEMETRY_MAIL_ENABLED=True, TELEMETRY_MAIL_MOVE_ENABLED=True), engine
    ).run()
    assert 1 in mailbox.folders["INBOX"]
    assert "MOVE" not in mailbox.commands
    assert scalar(engine, tables.mail_delivery) == 0


def test_duplicate_mail_uid_keeps_one_artifact_and_one_reading(engine, monkeypatch):
    """Dwie identyczne dostawy mają dwa potwierdzenia i jeden oryginał danych."""
    blob = message()
    mailbox = Mailbox({1: blob, 2: blob})
    install_mail(monkeypatch, mailbox)
    ImportRunner(
        config(TELEMETRY_MAIL_ENABLED=True, TELEMETRY_MAIL_MOVE_ENABLED=True), engine
    ).run()
    assert scalar(engine, tables.artifact) == 1
    assert scalar(engine, tables.record) == 1
    assert scalar(engine, tables.mail_delivery) == 2
    assert len(mailbox.folders["przetworzone"]) == 2


def test_mail_dry_run_has_no_write_effects(engine, monkeypatch):
    """Tryb kontrolny nie tworzy folderów, decyzji ani rekordów."""
    mailbox = Mailbox({1: message()})
    install_mail(monkeypatch, mailbox)
    ImportRunner(
        config(TELEMETRY_MAIL_ENABLED=True, TELEMETRY_MAIL_MOVE_ENABLED=True), dry_run=True
    ).run()
    assert set(mailbox.folders) == {"INBOX"}
    assert "MOVE" not in mailbox.commands
    assert scalar(engine, tables.record) == 0


def test_selected_sources_do_not_call_other_integrations(engine, tmp_path, monkeypatch):
    """Pilot CSV nie dotyka skrzynki ani źródeł bazodanowych."""
    monkeypatch.setattr("app.services.telemetry.csv_import.time.sleep", lambda _: None)
    (tmp_path / "DPLAC_test.csv").write_bytes(csv_blob())
    options = config(
        TELEMETRY_DPLAC_ROOT=str(tmp_path), TELEMETRY_MAIL_ENABLED=True, TELEMETRY_VM_ENABLED=True
    )
    result = ImportRunner(options, engine, selected=["remote_dplac"]).run()
    assert set(result) == {"remote_dplac"}


def test_daily_date_precision_and_cutoff_are_preserved():
    """Odczyt dzienny nie otrzymuje zmyślonej godziny, a starszy okres jest pomijany."""
    rows = [vm_row(1, ""), vm_row(2, "10:00", DATA="2023-01-01")]
    snapshots, _, counts = daily.prepare_page(
        "vmaintenance",
        "MASZYNY_STATS",
        rows,
        {7: "TEST-1"},
        {"TEST-1": [(1, 2)]},
        date(2024, 8, 1),
        date(2026, 8, 2),
    )
    assert daily.daily_reading(next(iter(snapshots.values()))).precision == "date"
    assert counts["excluded"] == 1


def test_intraday_repeated_alert_is_not_a_new_incident():
    """Ten sam stan z wielu próbek zachowuje wspólną tożsamość obserwacji."""
    snapshots, events, _ = daily.prepare_page(
        "vmaintenance",
        "MASZYNY_STATS",
        [vm_row(1, "10:00", error="stan testowy"), vm_row(2, "18:00", error="stan testowy")],
        {7: "TEST-1"},
        {"TEST-1": [(1, 2)]},
        date(2024, 8, 1),
        date(2026, 8, 2),
    )
    assert len(snapshots) == 1
    assert len({reading.external_key for reading in events}) == 1
    assert all(
        reading.kind == "event_observation" and reading.observed_at is None for reading in events
    )


def test_older_embedded_event_does_not_bypass_history_window():
    """Świeża migawka nie wprowadza do osi czasu zdarzenia spoza dwóch lat."""
    _, events, _ = daily.prepare_page(
        "vmaintenance",
        "MASZYNY_STATS",
        [vm_row(1, "10:00", jam_history=[{"date": "2019-01-01", "code": "old"}])],
        {7: "TEST-1"},
        {"TEST-1": [(1, 2)]},
        date(2024, 8, 1),
        date(2026, 8, 2),
    )
    assert not events


def test_undated_xml_replay_does_not_depend_on_generated_mime_boundary():
    """Techniczna granica MIME nie tworzy nowego zdarzenia przy ponownym parsowaniu."""
    envelope = EmailMessage()
    envelope["Subject"] = "Raport"
    envelope.add_attachment(
        b"<Report><DeviceID>TEST-1</DeviceID><MeterBlack>123</MeterBlack></Report>",
        maintype="application",
        subtype="xml",
        filename="report.xml",
    )
    blob = envelope.as_bytes()
    assert [reading.external_key for reading in parse_remote_message(blob)] == [
        reading.external_key for reading in parse_remote_message(blob)
    ]


def test_daily_normal_growth_is_not_a_conflict_but_equal_time_disagreement_is(engine):
    """Ostrzega o sprzecznym równoczesnym pomiarze, nie o późniejszym przyroście dnia."""

    def payload(row):
        snapshots, _, _ = daily.prepare_page(
            "vmaintenance",
            "MASZYNY_STATS",
            [row],
            {7: "TEST-1"},
            {"TEST-1": [(1, 2)]},
            date(2024, 8, 1),
            date(2026, 8, 2),
        )
        return next(iter(snapshots.values()))

    earlier = payload(vm_row(1, "10:00"))
    later = daily.merge_daily(earlier, payload(vm_row(2, "18:00")))
    with engine.begin() as connection:
        store = TelemetryStore(connection)
        source = store.ensure_source("daily-test", "test")
        store.ingest(source["id"], "first", [daily.daily_reading(earlier)])
        store.ingest(source["id"], "second", [daily.daily_reading(later)])
        assert (
            connection.execute(
                select(func.count())
                .select_from(tables.issue)
                .where(tables.issue.c.code == "conflicting_value")
            ).scalar_one()
            == 0
        )
    conflict = daily.merge_daily(later, payload(vm_row(3, "18:00")))
    reading = daily.daily_reading(conflict)
    assert ("conflicting_value", "lifetime.black") in reading.issues
    assert len(conflict["components"]["counters"]["alternatives"]) == 2


def test_daily_correction_reversion_points_to_existing_revision(engine):
    """Powtórzona korekta A/B/A/B nie pozostawia starej wersji jako bieżącej."""
    with engine.begin() as connection:
        store = TelemetryStore(connection)
        source = store.ensure_source("vmaintenance", "database")
        latest_ids = []
        for value in (100, 200, 100, 200, 200):
            payload = {
                "policy": daily.POLICY,
                "source": "vmaintenance",
                "serial": "TEST-1",
                "day": "2026-08-01",
                "components": {
                    "counters": {
                        "key": "same-source-row",
                        "time": "2026-08-01T10:00:00+00:00",
                        "data": {"counter": value},
                        "measurements": {"lifetime.black": value},
                    }
                },
            }
            key = "daily:TEST-1:2026-08-01"
            previous = daily.current_daily(connection, source["id"], key)
            reading = daily.daily_reading(daily.merge_daily(previous, payload))
            store.ingest(source["id"], "same-page", [reading])
            daily.accept_daily(connection, source["id"], [reading])
            current = daily.current_daily(connection, source["id"], key)
            assert current["components"]["counters"]["measurements"]["lifetime.black"] == value
            latest_ids.append(
                connection.execute(select(tables.daily_head.c.record_id)).scalar_one()
            )
        assert latest_ids[1] == latest_ids[3] == latest_ids[4]
        assert connection.execute(select(func.count()).select_from(tables.record)).scalar_one() == 3
        assert (
            connection.execute(select(func.count()).select_from(tables.daily_head)).scalar_one()
            == 1
        )
