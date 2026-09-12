"""Testy wskaźnika wersji dla ORBIT, wyłącznie na nietrwałej bazie SQLite."""

import gzip
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, select

from app.models import telemetry as tables
from app.services.telemetry import daily
from app.services.telemetry.parsers import PARSER_VERSION, Reading, fingerprint
from app.services.telemetry.store import TelemetryStore


@pytest.fixture
def engine():
    """Tworzy tabele telemetrii z kontrolą kluczy obcych, bez połączeń sieciowych."""
    database = create_engine(
        "sqlite://", execution_options={"schema_translate_map": {"ctip": None}}
    )

    @event.listens_for(database, "connect")
    def enable_foreign_keys(connection, _record):
        """Włącza sprawdzanie powiązań head i niezmiennej historii."""
        connection.execute("PRAGMA foreign_keys=ON")

    tables.Base.metadata.create_all(database, tables=list(tables.TELEMETRY_TABLES))
    yield database
    database.dispose()


def reading(value, key="record:1"):
    """Zwraca kolejne treści tego samego pomiaru, bez zmiany jego czasu."""
    return Reading(
        external_key=key,
        kind="counter",
        serial="HEAD-TEST",
        payload={"key": key, "value": value},
        observed_at=datetime(2026, 8, 1, 10, tzinfo=UTC),
        precision="second",
        time_basis="explicit",
        measurements={"lifetime.total": value},
        semantic_key=key,
    )


def save(engine, value, locator="page", *, source_kind="database", **kwargs):
    """Zatwierdza pojedynczą obserwację w osobnej transakcji."""
    with engine.begin() as connection:
        store = TelemetryStore(connection)
        source = store.ensure_source("head-test", source_kind)
        return store.ingest(source["id"], locator, [reading(value)], **kwargs)


def state(engine):
    """Odczytuje stan wszystkich tabel, aby wykryć częściowy zapis lub duplikację."""
    with engine.connect() as connection:
        return {
            table.name: [dict(row) for row in connection.execute(select(table)).mappings()]
            for table in tables.TELEMETRY_TABLES
        }


def head(engine):
    """Pobiera jedyny wskaźnik oraz przywraca UTC pomijane przez SQLite."""
    with engine.connect() as connection:
        result = dict(connection.execute(select(tables.record_head)).mappings().one())
    result["updated_at"] = result["updated_at"].replace(tzinfo=UTC)
    return result


def test_record_head_schema():
    """Wskaźnik ma złożony PK, wymagane FK oraz czas świadomy strefy."""
    assert tables.record_head in tables.TELEMETRY_TABLES
    assert list(tables.record_head.primary_key.columns.keys()) == ["source_id", "external_key"]
    assert {key.target_fullname for key in tables.record_head.foreign_keys} == {
        "ctip.telemetry_source.id",
        "ctip.telemetry_record.id",
    }
    assert not tables.record_head.c.record_id.nullable
    assert not tables.record_head.c.updated_at.nullable
    assert tables.record_head.c.updated_at.type.timezone


@pytest.mark.parametrize("same_locator", [True, False])
@pytest.mark.parametrize("source_kind", ["database", "csv"])
def test_a_b_a_reuses_original_revision_and_refreshes_observation(
    engine, monkeypatch, same_locator, source_kind
):
    """A/B/A/A aktualizuje head także przez marker, zachowując oryginały i idempotencję."""
    results, heads = [], []
    original_records = None
    for index, value in enumerate((100, 200, 100, 100)):
        accepted_at = datetime(2026, 9, 1, tzinfo=UTC) + timedelta(minutes=index)
        monkeypatch.setattr(
            "app.services.telemetry.store.utcnow", lambda accepted_at=accepted_at: accepted_at
        )
        kwargs = (
            {"blob": str(value).encode(), "embedded": True, "source_observation": True}
            if source_kind == "csv"
            else {}
        )
        results.append(
            save(
                engine,
                value,
                "page" if same_locator else f"page/{index}",
                source_kind=source_kind,
                **kwargs,
            )
        )
        heads.append(head(engine))
        assert heads[-1]["updated_at"] == accepted_at
        records = state(engine)[tables.record.name]
        selected = next(row for row in records if row["id"] == heads[-1]["record_id"])
        assert selected["payload"] == reading(value).payload
        if index == 1:
            original_records = records
        elif index > 1:
            assert records == original_records
    assert [result["new"] for result in results] == [1, 1, 0, 0]
    assert [result["duplicates"] for result in results] == [0, 0, 1, 1]
    assert heads[0]["record_id"] == heads[2]["record_id"] == heads[3]["record_id"]
    assert heads[0]["record_id"] != heads[1]["record_id"]
    snapshot = state(engine)
    assert len(snapshot[tables.record_head.name]) == 1
    assert len(snapshot[tables.record.name]) == 2
    assert len(snapshot[tables.imports.name]) == (2 if same_locator else 4)
    assert len(snapshot[tables.origin.name]) == (4 if same_locator else 8)
    assert len(snapshot[tables.artifact.name]) == (2 if source_kind == "csv" else 0)
    assert {row["code"] for row in snapshot[tables.issue.name]} == {"conflicting_value"}
    if same_locator:
        assert results[0]["marker"]["id"] == results[2]["marker"]["id"]
        assert results[2]["marker"]["id"] == results[3]["marker"]["id"]
    if source_kind == "csv":
        assert {gzip.decompress(row["content_gzip"]) for row in snapshot[tables.artifact.name]} == {
            b"100",
            b"200",
        }


@pytest.mark.parametrize("locator", ["report.csv", "archiwum/renamed.csv"])
@pytest.mark.parametrize("source_observation", [None, False])
def test_archival_csv_replay_does_not_rewind_authoritative_head(
    engine, locator, source_observation
):
    """Znany plik, także przemianowany, nie potwierdza cofnięcia źródła z B do A."""
    for value in (100, 200):
        save(
            engine,
            value,
            "report.csv",
            source_kind="csv",
            blob=str(value).encode(),
            source_observation=True,
        )
    confirmed = head(engine)
    records = state(engine)[tables.record.name]
    result = save(
        engine,
        100,
        locator,
        source_kind="csv",
        blob=b"100",
        source_observation=source_observation,
    )
    assert result["new"] == 0
    assert result["duplicates"] == 1
    assert head(engine) == confirmed
    assert state(engine)[tables.record.name] == records
    save(engine, 300, "archiwum/unseen.csv", source_kind="csv", blob=b"300")
    assert head(engine) == confirmed
    assert len(state(engine)[tables.record.name]) == 3


@pytest.mark.parametrize(
    "source_kind,blob,source_observation",
    [
        ("csv", None, None),
        ("imap", b"mail", None),
        ("database", b"dump", None),
        ("database", None, False),
    ],
)
def test_history_without_source_observation_does_not_fabricate_head(
    engine, source_kind, blob, source_observation
):
    """Niejednoznaczne wersje pozostają bez head dla fallbacku partial w ORBIT."""
    for value in (100, 200, 100):
        save(
            engine,
            value,
            source_kind=source_kind,
            blob=blob + str(value).encode() if blob is not None else None,
            source_observation=source_observation,
        )
    snapshot = state(engine)
    assert snapshot[tables.record_head.name] == []
    assert len(snapshot[tables.record.name]) == 2


def test_fresh_observation_of_legacy_marker_establishes_head(engine):
    """Nowa obserwacja A rozstrzyga starsze wersje bez kopiowania historii markerów."""
    first = save(engine, 100, source_observation=False)
    save(engine, 200, source_observation=False)
    before = state(engine)
    result = save(engine, 100)
    assert result["marker"]["id"] == first["marker"]["id"]
    after = state(engine)
    for table in (tables.record, tables.origin, tables.imports, tables.issue):
        assert after[table.name] == before[table.name]
    assert head(engine)["record_id"] == before[tables.record.name][0]["id"]


def test_empty_marker_replay_embeds_original_without_touching_head(engine):
    """Archiwizacja istniejącego markera bez readings zachowuje bieżącą wersję."""
    first = save(engine, 100, blob=b"original-a", source_observation=True, archive=True)
    save(engine, 200, blob=b"original-b", source_observation=True)
    confirmed = head(engine)
    before = state(engine)
    with engine.begin() as connection:
        store = TelemetryStore(connection)
        result = store.ingest(confirmed["source_id"], "page", [], blob=b"original-a", embedded=True)
    assert result["marker"]["id"] == first["marker"]["id"]
    assert result["marker"]["archive_status"] == "pending"
    assert head(engine) == confirmed
    after = state(engine)
    for table in (tables.record, tables.origin, tables.imports, tables.issue):
        assert after[table.name] == before[table.name]
    original = next(
        row for row in after[tables.artifact.name] if row["id"] == first["marker"]["artifact_id"]
    )
    assert gzip.decompress(original["content_gzip"]) == b"original-a"


@pytest.mark.parametrize(
    "previous_values,failed_value", [((), 100), ((100,), 200), ((100, 200), 100)]
)
def test_rollback_restores_head_records_origins_and_checkpoint(
    engine, previous_values, failed_value
):
    """Wycofuje nowy head, korektę i cofnięcie przez marker razem z resztą transakcji."""
    with engine.begin() as connection:
        source = TelemetryStore(connection).ensure_source("head-test", "database")
    for value in previous_values:
        save(engine, value)
    before = state(engine)
    with pytest.raises(RuntimeError, match="przerwanie"), engine.begin() as connection:
        store = TelemetryStore(connection)
        store.ingest(source["id"], "page", [reading(failed_value)])
        current = connection.execute(
            select(tables.record.c.payload).select_from(tables.record_head.join(tables.record))
        ).scalar_one()
        assert current == reading(failed_value).payload
        store.checkpoint(source["id"], {"cursor": 99})
        raise RuntimeError("przerwanie")
    assert state(engine) == before


def test_heads_are_isolated_by_source_and_external_key(engine):
    """Jedna obserwacja porcji aktualizuje wszystkie klucze tylko swojego źródła."""
    with engine.begin() as connection:
        store = TelemetryStore(connection)
        first_source = store.ensure_source("first", "database")
        second_source = store.ensure_source("second", "database")
        first_readings = [reading(100, "record:1"), reading(200, "record:2")]
        store.ingest(first_source["id"], "page", first_readings)
        store.ingest(second_source["id"], "page", [reading(300, "record:1")])
        store.ingest(first_source["id"], "page", [reading(400, "record:1")])
        store.ingest(first_source["id"], "page", first_readings)
        actual = connection.execute(
            select(
                tables.record_head.c.source_id,
                tables.record_head.c.external_key,
                tables.record.c.payload,
            ).select_from(tables.record_head.join(tables.record))
        ).all()
    assert {(row.source_id, row.external_key): row.payload["value"] for row in actual} == {
        (first_source["id"], "record:1"): 100,
        (first_source["id"], "record:2"): 200,
        (second_source["id"], "record:1"): 300,
    }


def test_daily_head_remains_authoritative(engine):
    """Ogólny head nie zmienia wyboru daily bez akceptacji polityki dziennej."""
    with engine.begin() as connection:
        store = TelemetryStore(connection)
        source = store.ensure_source("vmaintenance", "database")
        first = reading(100, "daily:HEAD-TEST:2026-08-01")
        second = reading(200, first.external_key)
        first.kind = second.kind = "daily_snapshot"
        store.ingest(source["id"], "page", [first])
        daily.accept_daily(connection, source["id"], [first])
        store.ingest(source["id"], "page", [second])
        daily.accept_daily(connection, source["id"], [second])
        store.ingest(source["id"], "page", [first])
        assert daily.current_daily(connection, source["id"], first.external_key) == second.payload
        current = connection.execute(
            select(tables.record.c.revision_hash, tables.record.c.parser_version).select_from(
                tables.record_head.join(tables.record)
            )
        ).one()
        assert current.revision_hash == fingerprint(first.payload)
        assert current.parser_version == PARSER_VERSION
        daily.accept_daily(connection, source["id"], [first])
        assert daily.current_daily(connection, source["id"], first.external_key) == first.payload
