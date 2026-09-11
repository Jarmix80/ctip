"""Izolowane testy pozyskiwania danych, bez dostępu do produkcji i źródeł sieciowych."""

import base64
import csv
import io
from datetime import UTC, datetime
from email.message import EmailMessage
from types import SimpleNamespace

import pytest
from defusedxml.common import EntitiesForbidden
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select

from app.api.deps import get_admin_session_context, get_db_session
from app.api.routes.admin_telemetry import router
from app.models import telemetry as tables
from app.services.telemetry.config import TelemetrySettings
from app.services.telemetry.parsers import number, parse_csv, parse_mail, timestamp
from app.services.telemetry.runner import ImportRunner, archive_committed
from app.services.telemetry.sources import material_identity, printradar_reading, vm_reading
from app.services.telemetry.store import TelemetryStore


def csv_blob(rows):
    """Tworzy niewielką, całkowicie syntetyczną próbkę CSV."""
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8-sig")


def counter_row(**values):
    """Zwraca podstawowy raport testowego urządzenia."""
    return {
        "Device Serial Number": "TEST-001",
        "Acquisition Date (mm/dd/yyyy)": "08/12/2026",
        "Acquisition Time": "10:00:00",
        "Counter Type": "pages",
        "Total": "1000",
        **values,
    }


@pytest.fixture
def engine():
    """Udostępnia wyłącznie siedem nowych tabel w nietrwałej bazie SQLite."""
    database = create_engine(
        "sqlite://",
        execution_options={"schema_translate_map": {"ctip": None}},
        connect_args={"check_same_thread": False},
    )
    tables.Base.metadata.create_all(database, tables=list(tables.TELEMETRY_TABLES))
    yield database
    database.dispose()


def ingest(engine, readings, locator="sample", **kwargs):
    """Wykonuje pełną transakcję importu próbki."""
    with engine.begin() as connection:
        store = TelemetryStore(connection)
        source = store.ensure_source("test", "csv")
        return store.ingest(source["id"], locator, readings, **kwargs)


def count(engine, table):
    """Zlicza wiersze bez uruchamiania kodu aplikacji produkcyjnej."""
    with engine.connect() as connection:
        return connection.execute(select(func.count()).select_from(table)).scalar_one()


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        ("", None),
        ("---", None),
        ("LOW !", None),
        ("OK !", None),
        ("NaN", None),
        ("Infinity", None),
        ("0", 0),
        ("10%", 10),
        ("3,5", 3.5),
    ],
)
def test_unknown_values_never_become_zero(value, expected):
    assert number(value) == expected


def test_csv_reporting_preserves_unknown_columns_and_toner():
    row = counter_row(**{"Toner Remaining(B)": "98", "Future Field": "dowolny tekst"})
    reading = parse_csv(csv_blob([row]))[0]
    assert reading.payload == row
    assert reading.measurements == {"lifetime.total": 1000, "toner.black.percent": 98}
    assert reading.observed_at == datetime(2026, 8, 12, 8, tzinfo=UTC)


@pytest.mark.parametrize(
    "payload",
    [
        b"Device Serial Number,Device Serial Number\na,b\n",
        b"foo,bar\na,b\n",
        b"Device Serial Number,Acquisition Date (mm/dd/yyyy)\na\n",
    ],
)
def test_bad_csv_is_not_partially_imported(payload):
    with pytest.raises(ValueError):
        parse_csv(payload)


def test_toner_and_all_supplies_share_sixteen_semantic_events(engine):
    rows = [
        {
            "Device Serial Number": f"TEST-{position:03}",
            "Occurrance Date (mm/dd/yyyy)": "08/12/2026",
            "Occurrance Time": "10:00",
            "Call Type": "Toner/Ink/AIO: Black",
        }
        for position in range(15)
    ]
    toner = parse_csv(csv_blob(rows))
    all_supplies = parse_csv(csv_blob([*rows, {**rows[0], "Call Type": "Waste Toner Bottle"}]))
    ingest(engine, toner, "toner")
    ingest(engine, all_supplies, "all_supplies")
    assert count(engine, tables.record) == 16
    assert len({reading.semantic_key for reading in toner + all_supplies}) == 16


def test_dplac_and_reporting_keep_richer_evidence_without_extra_semantic_reading(engine):
    basic = parse_csv(csv_blob([counter_row()]))
    full = parse_csv(csv_blob([counter_row(**{"Toner Remaining(B)": "50"})]))
    ingest(engine, basic, "dplac")
    ingest(engine, full, "reporting")
    assert count(engine, tables.record) == 2
    assert basic[0].semantic_key == full[0].semantic_key


def test_equal_counters_on_different_days_are_not_duplicates():
    first = parse_csv(csv_blob([counter_row()]))[0]
    second = parse_csv(csv_blob([counter_row(**{"Acquisition Date (mm/dd/yyyy)": "08/13/2026"})]))[
        0
    ]
    assert first.semantic_key != second.semantic_key


def test_not_obtained_preserves_old_acquisition_and_window():
    row = {
        "Device Serial Number": "TEST-001",
        "Last Acquisition Date (mm/dd/yyyy)": "01/01/2025",
        "Last Acquisition Time": "10:00",
        "Last Total": "123",
        "Starting date": "09/07/2026",
        "End date": "09/09/2026",
    }
    reading = parse_csv(csv_blob([row]))[0]
    assert reading.kind == "unavailable"
    assert reading.observed_at.year == 2025
    assert reading.payload["End date"] == "09/09/2026"
    assert reading.measurements["lifetime.total"] == 123


def test_timestamp_rejects_ambiguous_or_nonexistent_local_time():
    assert timestamp("10/25/2026", "02:30")[2] == "ambiguous"
    assert timestamp("03/29/2026", "02:30")[2] == "ambiguous"
    assert timestamp("08/12/2026", "10:00", "")[2] == "unresolved"


def test_reimport_is_idempotent_and_keeps_distinct_artifact_origins(engine):
    blob = csv_blob([counter_row()])
    readings = parse_csv(blob)
    assert ingest(engine, readings, "first", blob=blob)["new"] == 1
    assert ingest(engine, readings, "first", blob=blob)["new"] == 0
    assert ingest(engine, readings, "renamed", blob=blob)["new"] == 0
    assert count(engine, tables.record) == 1
    assert count(engine, tables.artifact) == 1
    assert count(engine, tables.origin) == 4


def test_changed_source_record_creates_version(engine):
    first = parse_csv(csv_blob([counter_row()]))
    corrected = parse_csv(csv_blob([counter_row(Total="1001")]))
    ingest(engine, first)
    ingest(engine, corrected)
    assert count(engine, tables.record) == 2
    with engine.connect() as connection:
        assert (
            "conflicting_value" in connection.execute(select(tables.issue.c.code)).scalars().all()
        )


def test_late_history_checks_both_neighbors(engine):
    newer = parse_csv(
        csv_blob([counter_row(Total="100", **{"Acquisition Date (mm/dd/yyyy)": "08/13/2026"})])
    )
    older = parse_csv(csv_blob([counter_row(Total="200")]))
    ingest(engine, newer)
    ingest(engine, older)
    with engine.connect() as connection:
        issue = (
            connection.execute(
                select(tables.issue).where(tables.issue.c.code == "counter_decrease")
            )
            .mappings()
            .one()
        )
        assert issue["record_id"] != issue["related_record_id"]


def test_bottle_reset_and_toner_increase_are_not_lifetime_decrease(engine):
    first = parse_csv(
        csv_blob(
            [
                counter_row(
                    **{"Black counter by current toner (Page)": "1000", "Toner Remaining(B)": "10"}
                )
            ]
        )
    )
    second = parse_csv(
        csv_blob(
            [
                counter_row(
                    **{
                        "Acquisition Date (mm/dd/yyyy)": "08/13/2026",
                        "Black counter by current toner (Page)": "0",
                        "Toner Remaining(B)": "100",
                    }
                )
            ]
        )
    )
    ingest(engine, first)
    ingest(engine, second)
    assert count(engine, tables.issue) == 0


def test_invalid_measurements_are_retained_and_flagged(engine):
    ingest(engine, parse_csv(csv_blob([counter_row(Total="-1", **{"Toner Remaining(B)": "101"})])))
    assert count(engine, tables.record) == 1
    assert count(engine, tables.issue) == 2


def test_failed_transaction_does_not_leave_checkpoint_or_partial_records(engine):
    with pytest.raises(RuntimeError), engine.begin() as connection:
        store = TelemetryStore(connection)
        source = store.ensure_source("test", "csv")
        store.ingest(source["id"], "partial", parse_csv(csv_blob([counter_row()])))
        store.checkpoint(source["id"], {"cursor": 42})
        raise RuntimeError("przerwanie")
    assert count(engine, tables.record) == 0
    assert count(engine, tables.source) == 0


def test_archive_after_commit_and_recovery_does_not_repeat_import(engine, tmp_path):
    path = tmp_path / "report.csv"
    blob = csv_blob([counter_row()])
    path.write_bytes(blob)
    result = ingest(engine, parse_csv(blob), str(path), blob=blob, embedded=True, archive=True)
    assert path.exists()
    archive_committed(engine, result["marker"])
    assert not path.exists()
    assert (tmp_path / "archiwum" / "report.csv").read_bytes() == blob
    archive_committed(
        engine, {**result["marker"], "archive_path": str(tmp_path / "archiwum" / "report.csv")}
    )
    assert count(engine, tables.record) == 1


def test_archive_never_overwrites_other_file(engine, tmp_path):
    directory = tmp_path / "archiwum"
    directory.mkdir()
    (directory / "report.csv").write_bytes(b"wczesniejszy plik")
    path = tmp_path / "report.csv"
    blob = csv_blob([counter_row()])
    path.write_bytes(blob)
    result = ingest(engine, parse_csv(blob), str(path), blob=blob, archive=True)
    archive_committed(engine, result["marker"])
    assert (directory / "report.csv").read_bytes() == b"wczesniejszy plik"
    assert len(list(directory.iterdir())) == 2


def test_archive_refuses_changed_source(engine, tmp_path):
    path = tmp_path / "report.csv"
    blob = csv_blob([counter_row()])
    path.write_bytes(blob)
    result = ingest(engine, parse_csv(blob), str(path), blob=blob, archive=True)
    path.write_bytes(b"inna zawartosc")
    with pytest.raises(ValueError, match="archive_source_changed"):
        archive_committed(engine, result["marker"])
    assert path.exists()


def test_dplac_is_untouched_and_not_embedded(engine, tmp_path, monkeypatch):
    path = tmp_path / "DPLAC_test.csv"
    blob = csv_blob([counter_row()])
    path.write_bytes(blob)
    signature = path.stat().st_mtime_ns
    config = TelemetrySettings(
        _env_file=None, TELEMETRY_ENABLED=True, TELEMETRY_DPLAC_ROOT=str(tmp_path)
    )
    monkeypatch.setattr("app.services.telemetry.runner.time.sleep", lambda _: None)
    ImportRunner(config, engine).run()
    ImportRunner(config, engine).run()
    assert path.read_bytes() == blob
    assert path.stat().st_mtime_ns == signature
    assert not (tmp_path / "archiwum").exists()
    assert count(engine, tables.record) == 1
    with engine.connect() as connection:
        assert connection.execute(select(tables.artifact.c.content_gzip)).scalar() is None


def test_dry_run_never_uses_database_or_creates_archive(tmp_path):
    report = tmp_path / "Reporting"
    report.mkdir()
    (report / "report.csv").write_bytes(csv_blob([counter_row()]))
    config = TelemetrySettings(
        _env_file=None, TELEMETRY_ENABLED=True, TELEMETRY_REPORT_ROOT=str(tmp_path)
    )
    result = ImportRunner(config, dry_run=True).run()
    assert result["remote_reporting"]["new"] == 1
    assert not (report / "archiwum").exists()


def test_mail_text_and_xml_share_event_key_without_guessing_timezone():
    messages = []
    for xml in (False, True):
        message = EmailMessage()
        message["Subject"] = "@Remote Supply Call, Device S/N: TEST-001"
        message["Date"] = "Wed, 12 Aug 2026 10:00:00 +0200"
        if xml:
            message.set_content("Powiadomienie")
            message.add_attachment(
                b"<Call><DeviceID>TEST-001</DeviceID><ReceiveDate>08/12/2026 10:00:00</ReceiveDate><MeterBlack>100</MeterBlack></Call>",
                maintype="application",
                subtype="xml",
                filename="call.xml",
            )
        else:
            message.set_content(
                "1.Device S/N: TEST-001\n2.Receive date and time: 08/12/2026 10:00:00\n3.Meter (Color/Black): 0/100"
            )
        messages.append(parse_mail(message.as_bytes())[0])
    assert messages[0].semantic_key == messages[1].semantic_key
    assert messages[0].observed_at is None
    assert messages[0].received_at is not None
    assert (
        messages[0].measurements["lifetime.black"]
        == messages[1].measurements["lifetime.black"]
        == 100
    )


def test_xml_entities_are_rejected():
    message = EmailMessage()
    message.set_content("test")
    message.add_attachment(
        b'<!DOCTYPE call [<!ENTITY bad SYSTEM "file:///etc/passwd">]><call>&bad;</call>',
        maintype="application",
        subtype="xml",
        filename="call.xml",
    )
    with pytest.raises(EntitiesForbidden):
        parse_mail(message.as_bytes())


@pytest.mark.parametrize(
    "description,expected",
    [
        ("toner czarny", ("toner", "black")),
        ("toner żółty", ("toner", "yellow")),
        ("Toner schwarz", ("toner", "black")),
        ("zużyty toner", ("waste_toner", "")),
        ("toner", ("toner_unknown", "")),
    ],
)
def test_material_aliases_do_not_invent_extra_toners(description, expected):
    assert material_identity({"description": description}) == expected


def test_vmaintenance_preserves_text_status_and_source_stock():
    reading = vm_reading(
        "MASZYNY",
        {
            "ID_TBL_MASZYNY": 1,
            "ID_MASZYNA": 9,
            "ID_NR_SERYJNY": "TEST-001",
            "DATA_LICZNIKA": "2026-08-12",
            "T_BK": "LOW !",
            "ZAPAS_BK": 2,
        },
        {},
    )
    assert "toner.black.percent" not in reading.measurements
    assert reading.payload["ZAPAS_BK"] == 2
    assert reading.precision == "date"


def test_vmaintenance_preserves_nul_text_without_invalid_jsonb(engine):
    """Zachowuje NUL i polskie znaki bez usuwania treści oraz bez kolizji z tekstem ucieczki."""
    from app.services.telemetry.sources import serializable

    original = "Łódź\x00opis"
    converted = serializable({"text": [original, r"Łódź\u0000opis"]})
    encoded = converted["text"][0]
    assert encoded["__telemetry_encoding__"] == "utf-8/base64"
    assert base64.b64decode(encoded["value"]).decode("utf-8") == original
    assert converted["text"][1] == r"Łódź\u0000opis"
    reading = vm_reading(
        "WEZWANIE", {"ID_TBL_WEZWANIE": 1, "OPIS": original, "ID_MASZYNA": 1}, {1: "TEST"}
    )
    assert reading.payload["OPIS"] == encoded
    assert ("source_text_encoded", "OPIS") in reading.issues
    assert ingest(engine, [reading])["new"] == 1
    assert ingest(engine, [reading])["new"] == 0


def test_printradar_material_aliases_share_semantic_key():
    rows = [
        {
            "id": 1,
            "snapshot_id": "snapshot",
            "fingerprint_key": "device",
            "collected_at": "2026-08-12T10:00:00+00:00",
            "description": description,
            "percent": 50,
            "unit": "%",
        }
        for description in ("toner czarny", "toner black")
    ]
    readings = [
        printradar_reading("material_readings", row, {"device": "TEST-001"}) for row in rows
    ]
    assert readings[0].semantic_key == readings[1].semantic_key


def test_ambiguous_ms_serial_is_not_automatically_assigned(engine):
    ingest(engine, parse_csv(csv_blob([counter_row()])))
    with engine.begin() as connection:
        store = TelemetryStore(connection)
        source = store.ensure_source("test", "csv")
        store.bind_devices(source["id"], {"TEST-001": [(1, 10), (2, 20)]})
        link = connection.execute(select(tables.device_link)).mappings().one()
        assert link["status"] == "ambiguous"
        assert link["ms_machine_id"] is None


def test_admin_api_rejects_non_admin_before_database_access():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_admin_session_context] = lambda: (
        None,
        SimpleNamespace(role="operator"),
    )
    app.dependency_overrides[get_db_session] = lambda: None
    assert TestClient(app).get("/admin/telemetry/status").status_code == 403


def test_mail_uidvalidity_reset_keeps_all_messages_eligible():
    from app.services.telemetry.sources import mail_uids

    class Mailbox:
        def response(self, _):
            return "UIDVALIDITY", [b"new-generation"]

        def uid(self, *arguments):
            assert arguments == ("search", None, "UID", "1:*")
            return "OK", [b"1 2 3"]

    assert mail_uids(Mailbox(), {"uidvalidity": "old-generation", "uid": 100}) == (
        "new-generation",
        [1, 2, 3],
    )


def test_failed_database_page_does_not_advance_cursor_with_next_table(engine, monkeypatch):
    from app.services.telemetry import sources

    config = TelemetrySettings(_env_file=None, TELEMETRY_ENABLED=True, TELEMETRY_VM_ENABLED=True)
    monkeypatch.setattr(
        sources, "VM_TABLES", {"MASZYNY_STATS": "ID_TBL_MASZYNY_STATS", "MAGAZYNY": "ID_MAGAZYN"}
    )
    monkeypatch.setattr(sources, "vm_serials", lambda _: {})
    monkeypatch.setattr(
        sources,
        "vm_page",
        lambda table, *_: (
            [{"ID_TBL_MASZYNY_STATS": 99, "DATA": "2026-08-12"}] if table == "MASZYNY_STATS" else []
        ),
    )
    original = TelemetryStore.ingest

    def fail_history(store, source_id, locator, *args, **kwargs):
        if locator.startswith("MASZYNY_STATS/"):
            raise RuntimeError("przerwana transakcja")
        return original(store, source_id, locator, *args, **kwargs)

    monkeypatch.setattr(TelemetryStore, "ingest", fail_history)
    ImportRunner(config, engine).run_database("vmaintenance")
    with engine.connect() as connection:
        checkpoint = connection.execute(select(tables.source.c.checkpoint)).scalar_one()
        assert checkpoint.get("MASZYNY_STATS", {}).get("watermark") is None


def test_unmatched_serial_can_be_resolved_later(engine):
    ingest(engine, parse_csv(csv_blob([counter_row()])))
    with engine.begin() as connection:
        store = TelemetryStore(connection)
        source = store.ensure_source("test", "csv")
        store.bind_devices(source["id"], {})
        store.bind_devices(source["id"], {"TEST-001": [(1, 10)]})
        link = (
            connection.execute(
                select(tables.device_link).where(tables.device_link.c.valid_to.is_(None))
            )
            .mappings()
            .one()
        )
        assert link["status"] == "matched"
        assert connection.execute(select(tables.record.c.device_link_id)).scalar_one() == link["id"]


def test_date_only_reading_does_not_invent_intraday_counter_regression(engine):
    newer = parse_csv(csv_blob([counter_row(Total="100")]))
    date_only = parse_csv(csv_blob([counter_row(Total="200", **{"Acquisition Time": ""})]))
    ingest(engine, newer)
    ingest(engine, date_only)
    with engine.connect() as connection:
        assert (
            connection.execute(
                select(func.count())
                .select_from(tables.issue)
                .where(tables.issue.c.code == "counter_decrease")
            ).scalar_one()
            == 0
        )


def test_test_profile_rejects_production_host(monkeypatch):
    from app.services.telemetry.config import check_test_host

    monkeypatch.setattr("app.services.telemetry.config.settings.ctip_runtime_profile", "test")
    monkeypatch.setattr(
        "app.services.telemetry.config.socket.getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("192.168.0.8", 3050))],
    )
    with pytest.raises(ValueError, match="test_source_not_local"):
        check_test_host("production")


def test_mail_toner_levels_are_extracted():
    message = EmailMessage()
    message.set_content(
        "1.Device S/N: TEST-001\n2.Toner Status: Black 100%,Yellow 50%,Magenta 0%,Cyan 60%"
    )
    reading = parse_mail(message.as_bytes())[0]
    assert reading.measurements["toner.magenta.percent"] == 0
    assert reading.measurements["toner.black.percent"] == 100


def test_imap_batch_uses_peek_and_filters_oversized_messages():
    from app.services.telemetry.sources import mail_batch

    class Mailbox:
        def uid(self, action, identifiers, query):
            assert action == "fetch"
            if query == "(UID RFC822.SIZE)":
                assert identifiers == "10,11"
                return "OK", [b"1 (UID 10 RFC822.SIZE 4)", b"2 (UID 11 RFC822.SIZE 500)"]
            assert query == "(UID BODY.PEEK[])"
            assert identifiers == "10"
            return "OK", [(b"1 (UID 10 BODY[] {4}", b"mail"), b")"]

    assert mail_batch(Mailbox(), [10, 11], 100) == {10: b"mail"}


def test_admin_page_escapes_source_paths_and_exposes_status(engine):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_admin_session_context] = lambda: (
        None,
        SimpleNamespace(role="admin"),
    )
    ingest(engine, parse_csv(csv_blob([counter_row()])), "<script>alert(1)</script>")
    with engine.connect() as connection:

        class Session:
            async def execute(self, query):
                return connection.execute(query)

        app.dependency_overrides[get_db_session] = Session
        client = TestClient(app)
        assert client.get("/admin/telemetry/status").json()["records"] == 1
        response = client.get("/admin/telemetry")
        assert response.status_code == 200
        assert "<script>alert(1)</script>" not in response.text
        assert "&lt;script&gt;" in response.text


def test_firebird_25_reader_discovery_uses_services_not_security_table(monkeypatch):
    from scripts import setup_telemetry_windows as setup

    username = setup.READER.upper().encode()

    class Service:
        def _op_service_start(self, request):
            assert request.endswith(username)

        def _op_service_info(self, send, receive):
            assert receive == bytes([68])

        def _op_response(self):
            payload = bytes([7]) + len(username).to_bytes(2, "little") + username
            return (
                None,
                None,
                bytes([68]) + len(payload).to_bytes(2, "little") + payload + bytes([1]),
            )

        def close(self):
            pass

    monkeypatch.setattr(setup.services, "connect", lambda **kwargs: Service())
    assert setup.firebird_reader_exists(
        {"FB_HOST": "localhost", "FB_USER": "test", "FB_PASSWORD": "test"}
    )


def test_vmaintenance_uses_utf8_without_changing_ms_charset(monkeypatch):
    """Rozdziela UTF-8 pól NONE w V od połączenia WIN1250 używanego przez MS."""
    from app.services.telemetry import sources
    from scripts import setup_telemetry_windows as setup

    connections = []

    class Connection:
        def cursor(self):
            return self

        def execute(self, query):
            assert "MON$READ_ONLY" in query

        def fetchone(self):
            return (1,)

        def rollback(self):
            pass

        def close(self):
            pass

    def connect(**kwargs):
        connections.append(kwargs)
        return Connection()

    monkeypatch.delenv("TELEMETRY_VM_CHARSET", raising=False)
    monkeypatch.setattr(sources.settings, "fb_charset", "WIN1250")
    monkeypatch.setattr(sources.firebirdsql, "connect", connect)
    config = TelemetrySettings(_env_file=None)
    assert config.vm_charset == setup.VM_CHARSET == "UTF8"
    for vmaintenance in (True, False):
        with sources.firebird_connection(vmaintenance, config):
            pass
    assert [connection["charset"] for connection in connections] == ["UTF8", "WIN1250"]
    assert all(
        connection["isolation_level"] == sources.firebirdsql.ISOLATION_LEVEL_READ_COMMITED_RO
        for connection in connections
    )


def test_redelivered_report_is_archived_without_duplicate_data(engine, tmp_path, monkeypatch):
    for name in ("Toner", "All Supplies", "Reporting"):
        (tmp_path / name).mkdir()
    path = tmp_path / "Reporting" / "report.csv"
    blob = csv_blob([counter_row()])
    config = TelemetrySettings(
        _env_file=None, TELEMETRY_ENABLED=True, TELEMETRY_REPORT_ROOT=str(tmp_path)
    )
    monkeypatch.setattr("app.services.telemetry.runner.time.sleep", lambda _: None)
    for attempt in range(2):
        path.write_bytes(blob)
        ImportRunner(config, engine).run()
        assert not path.exists(), attempt
    assert count(engine, tables.record) == 1
    assert len(list((path.parent / "archiwum").iterdir())) == 2
