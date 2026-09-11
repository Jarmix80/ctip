"""Transakcyjny rejestr importów, pochodzenia i niezmiennych wersji danych."""

import gzip
import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, insert, or_, select, update

from app.models import telemetry as tables
from app.services.telemetry.parsers import PARSER_VERSION, Reading, fingerprint


def utcnow() -> datetime:
    """Zwraca czas zapisu w UTC."""
    return datetime.now(UTC)


class TelemetryStore:
    """Zapisuje dane tylko do tabel telemetrycznych w przekazanej transakcji."""

    def __init__(self, connection):
        self.connection = connection

    def add(self, table, **values):
        """Dodaje pojedynczy rekord ze stabilnym identyfikatorem technicznym."""
        values.setdefault("id", str(uuid4()))
        self.connection.execute(insert(table).values(**values))
        return values["id"]

    def ensure_source(self, name: str, kind: str):
        """Tworzy źródło bez przechowywania poświadczeń."""
        current = (
            self.connection.execute(select(tables.source).where(tables.source.c.name == name))
            .mappings()
            .first()
        )
        if current:
            self.connection.execute(
                update(tables.source)
                .where(tables.source.c.id == current["id"])
                .values(enabled=True)
            )
            return dict(current)
        identifier = self.add(tables.source, name=name, kind=kind, enabled=True, checkpoint={})
        return {"id": identifier, "checkpoint": {}, "name": name, "kind": kind}

    def checkpoint(self, source_id: str, value: dict):
        """Zatwierdza punkt wznowienia w tej samej transakcji co dane."""
        self.connection.execute(
            update(tables.source)
            .where(tables.source.c.id == source_id)
            .values(checkpoint=value, last_success_at=utcnow(), last_error=None)
        )

    def failure(self, source_id: str, locator: str, error_code: str):
        """Rejestruje kod błędu bez niebezpiecznej treści wyjątku sterownika."""
        self.add(
            tables.imports,
            source_id=source_id,
            locator=locator,
            started_at=utcnow(),
            finished_at=utcnow(),
            status="error",
            counts={},
            error_code=error_code,
        )
        self.connection.execute(
            update(tables.source)
            .where(tables.source.c.id == source_id)
            .values(last_error=error_code)
        )

    def file_marker(self, source_id: str, locator: str, digest: str):
        """Odnajduje zakończony import konkretnej wersji pliku i parsera."""
        return (
            self.connection.execute(
                select(tables.origin).where(
                    tables.origin.c.source_id == source_id,
                    tables.origin.c.locator == locator,
                    tables.origin.c.position == "__artifact__",
                    tables.origin.c.fingerprint == digest + ":" + PARSER_VERSION,
                )
            )
            .mappings()
            .first()
        )

    def ingest(
        self,
        source_id: str,
        locator: str,
        readings: list[Reading],
        *,
        blob: bytes | None = None,
        media_type="application/json",
        embedded=False,
        archive=False,
    ):
        """Atomowo zapisuje cały plik lub porcję bazy przed dopuszczeniem archiwizacji."""
        digest = (
            hashlib.sha256(blob).hexdigest()
            if blob is not None
            else fingerprint([row.payload for row in readings])
        )
        marker = self.file_marker(source_id, locator, digest)
        artifact_id = None
        if blob is not None:
            artifact_id = self.connection.execute(
                select(tables.artifact.c.id).where(tables.artifact.c.sha256 == digest)
            ).scalar()
            if artifact_id is None:
                artifact_id = self.add(
                    tables.artifact,
                    sha256=digest,
                    size_bytes=len(blob),
                    media_type=media_type,
                    content_gzip=gzip.compress(blob, mtime=0) if embedded else None,
                    created_at=utcnow(),
                )
            elif embedded:
                self.connection.execute(
                    update(tables.artifact)
                    .where(
                        tables.artifact.c.id == artifact_id,
                        tables.artifact.c.content_gzip.is_(None),
                    )
                    .values(content_gzip=gzip.compress(blob, mtime=0))
                )
        if marker:
            return {"new": 0, "duplicates": len(readings), "marker": dict(marker)}
        run_id = self.add(
            tables.imports,
            source_id=source_id,
            locator=locator,
            started_at=utcnow(),
            status="running",
            counts={},
        )
        counts = {"new": 0, "duplicates": 0, "rows": len(readings)}
        for position, reading in enumerate(readings, 1):
            revision = fingerprint(reading.payload)
            existing = self.connection.execute(
                select(tables.record.c.id).where(
                    tables.record.c.source_id == source_id,
                    tables.record.c.external_key == reading.external_key,
                    tables.record.c.revision_hash == revision,
                    tables.record.c.parser_version == PARSER_VERSION,
                )
            ).scalar()
            if existing:
                record_id = existing
                counts["duplicates"] += 1
            else:
                record_id = self.add(
                    tables.record,
                    source_id=source_id,
                    external_key=reading.external_key,
                    revision_hash=revision,
                    parser_version=PARSER_VERSION,
                    semantic_key=reading.semantic_key
                    or fingerprint([source_id, reading.external_key]),
                    kind=reading.kind,
                    serial=reading.serial or None,
                    observed_at=reading.observed_at,
                    time_precision=reading.precision,
                    time_basis=reading.time_basis,
                    received_at=reading.received_at,
                    imported_at=utcnow(),
                    payload=reading.payload,
                    measurements=reading.measurements,
                    device_link_id=self.billing_link(source_id, reading),
                )
                self.validate(record_id, source_id, reading)
                counts["new"] += 1
            self.add(
                tables.origin,
                source_id=source_id,
                import_id=run_id,
                artifact_id=artifact_id,
                record_id=record_id,
                locator=locator,
                position=reading.origin_key or str(position),
                fingerprint=digest + ":" + PARSER_VERSION,
                archive_status="not_required",
            )
        marker_id = self.add(
            tables.origin,
            source_id=source_id,
            import_id=run_id,
            artifact_id=artifact_id,
            locator=locator,
            position="__artifact__",
            fingerprint=digest + ":" + PARSER_VERSION,
            archive_status="pending" if archive else "not_required",
        )
        self.connection.execute(
            update(tables.imports)
            .where(tables.imports.c.id == run_id)
            .values(finished_at=utcnow(), status="imported", counts=counts)
        )
        self.connection.execute(
            update(tables.source)
            .where(tables.source.c.id == source_id)
            .values(last_success_at=utcnow(), last_error=None)
        )
        return {
            **counts,
            "marker": {
                "id": marker_id,
                "artifact_id": artifact_id,
                "locator": locator,
                "archive_status": "pending" if archive else "not_required",
                "archive_path": None,
                "fingerprint": digest + ":" + PARSER_VERSION,
            },
        }

    def billing_link(self, source_id: str, reading: Reading):
        """Zachowuje klienta z historycznego CPC zamiast dzisiejszego właściciela numeru seryjnego."""
        if reading.kind != "billing_period":
            return None
        machine, customer = (reading.payload.get(key) for key in ("ID_MASZYNA", "ID_KLIENT"))
        if not all(isinstance(value, int) and value > 0 for value in (machine, customer)):
            return None
        key = f"CPC:{machine}:{customer}"
        current = self.connection.execute(
            select(tables.device_link.c.id).where(
                tables.device_link.c.source_id == source_id,
                tables.device_link.c.external_key == key,
            )
        ).scalar()
        return current or self.add(
            tables.device_link,
            source_id=source_id,
            external_key=key,
            serial=reading.serial or None,
            ms_machine_id=machine,
            ms_customer_id=customer,
            status="source_confirmed",
            valid_from=utcnow(),
        )

    def add_issue(self, record_id: str, code: str, metric: str = "", related=None, **details):
        """Dodaje idempotentne ostrzeżenie, zachowując podejrzany pomiar."""
        present = self.connection.execute(
            select(tables.issue.c.id).where(
                tables.issue.c.record_id == record_id,
                tables.issue.c.code == code,
                tables.issue.c.metric == metric,
            )
        ).scalar()
        if not present:
            self.add(
                tables.issue,
                record_id=record_id,
                code=code,
                metric=metric,
                related_record_id=related,
                details=details,
                created_at=utcnow(),
            )

    def validate(self, record_id: str, source_id: str, reading: Reading):
        """Sprawdza zakresy i oba sąsiedztwa czasowe, bez korygowania wartości."""
        if reading.kind == "billing_period":
            self.validate_billing(record_id, source_id, reading)
        if not reading.serial:
            self.add_issue(record_id, "missing_serial")
        if reading.observed_at is None and reading.kind != "billing_period":
            self.add_issue(record_id, "time_" + reading.time_basis)
        elif reading.observed_at is not None and reading.observed_at > utcnow() + timedelta(days=1):
            self.add_issue(record_id, "future_time")
        for code, metric in reading.issues:
            self.add_issue(record_id, code, metric)
        for metric, value in reading.measurements.items():
            if not isinstance(value, (int, float)):
                continue
            if value < 0 or (metric.endswith(".percent") and value > 100):
                self.add_issue(record_id, "invalid_range", metric, value=value)
                continue
            if (
                not metric.startswith("lifetime.")
                or not reading.serial
                or reading.observed_at is None
                or reading.kind == "unavailable"
            ):
                continue
            query = select(tables.record).where(
                tables.record.c.source_id == source_id,
                tables.record.c.serial == reading.serial,
                tables.record.c.id != record_id,
                tables.record.c.kind != "unavailable",
                tables.record.c.time_basis == reading.time_basis,
                tables.record.c.measurements[metric].as_float().is_not(None),
            )
            for previous in (True, False):
                condition = (
                    tables.record.c.observed_at < reading.observed_at
                    if previous
                    else tables.record.c.observed_at > reading.observed_at
                )
                order = (
                    tables.record.c.observed_at.desc()
                    if previous
                    else tables.record.c.observed_at.asc()
                )
                neighbor = (
                    self.connection.execute(query.where(condition).order_by(order).limit(1))
                    .mappings()
                    .first()
                )
                if not neighbor:
                    continue
                neighbor_time = neighbor["observed_at"]
                if neighbor_time.tzinfo is None:
                    neighbor_time = neighbor_time.replace(tzinfo=UTC)
                if "date" in (reading.precision, neighbor["time_precision"]) and abs(
                    reading.observed_at - neighbor_time
                ) < timedelta(days=1):
                    continue
                if neighbor["payload"].get("Counter Type") != reading.payload.get("Counter Type"):
                    continue
                neighbor_value = neighbor["measurements"][metric]
                if previous and value < neighbor_value:
                    self.add_issue(
                        record_id,
                        "counter_decrease",
                        metric,
                        neighbor["id"],
                        previous=neighbor_value,
                        value=value,
                    )
                elif not previous and neighbor_value < value:
                    self.add_issue(
                        neighbor["id"],
                        "counter_decrease",
                        metric,
                        record_id,
                        previous=value,
                        value=neighbor_value,
                    )
        if reading.semantic_key and reading.kind != "daily_snapshot":
            matches = self.connection.execute(
                select(tables.record).where(
                    tables.record.c.semantic_key == reading.semantic_key,
                    tables.record.c.id != record_id,
                )
            ).mappings()
            for other in matches:
                for metric in reading.measurements.keys() & other["measurements"].keys():
                    if reading.measurements[metric] != other["measurements"][metric]:
                        self.add_issue(record_id, "conflicting_value", metric, other["id"])

    def validate_billing(self, record_id: str, source_id: str, reading: Reading):
        """Porównuje zafakturowane okresy tej samej maszyny i umowy, nie daty wystawienia FV."""
        period = reading.payload["__ctip_billing__"]
        if not period["start"] or not period["invoice_linked"]:
            return
        period_column = tables.record.c.payload["__ctip_billing__"]["start"].as_string()
        query = select(tables.record).where(
            tables.record.c.source_id == source_id,
            tables.record.c.kind == "billing_period",
            tables.record.c.serial == (reading.serial or None),
            tables.record.c.id != record_id,
            tables.record.c.payload["ID_MASZYNA"].as_integer() == reading.payload.get("ID_MASZYNA"),
            tables.record.c.payload["ID_UMOWACPC"].as_integer()
            == reading.payload.get("ID_UMOWACPC"),
            tables.record.c.payload["__ctip_billing__"]["invoice_linked"].as_boolean().is_(True),
        )
        for previous in (True, False):
            condition = (
                period_column < period["start"] if previous else period_column > period["start"]
            )
            order = period_column.desc() if previous else period_column.asc()
            neighbor = (
                self.connection.execute(
                    query.where(condition)
                    .order_by(order, tables.record.c.imported_at.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if neighbor is None:
                continue
            for metric, value in reading.measurements.items():
                other = neighbor["measurements"].get(metric)
                if not metric.startswith("billing.end.") or other is None:
                    continue
                if (previous and value < other) or (not previous and other < value):
                    self.add_issue(
                        record_id if previous else neighbor["id"],
                        "billing_period_decrease",
                        metric,
                        neighbor["id"] if previous else record_id,
                    )

    def bind_devices(self, source_id: str, mapping: dict):
        """Wersjonuje wyłącznie jednoznaczne powiązania MS bez przepisywania historii."""
        unresolved = and_(
            tables.record.c.kind != "billing_period",
            or_(
                tables.record.c.device_link_id.is_(None),
                tables.record.c.device_link_id.in_(
                    select(tables.device_link.c.id).where(
                        tables.device_link.c.status.in_(["unmatched", "ambiguous"])
                    )
                ),
            ),
        )
        identities = (
            self.connection.execute(
                select(tables.record.c.serial)
                .where(
                    tables.record.c.source_id == source_id,
                    unresolved,
                    tables.record.c.serial.is_not(None),
                )
                .distinct()
            )
            .scalars()
            .all()
        )
        for serial in identities:
            candidates = mapping.get(serial, [])
            status = (
                "matched" if len(candidates) == 1 else "ambiguous" if candidates else "unmatched"
            )
            machine, customer = candidates[0] if status == "matched" else (None, None)
            current = (
                self.connection.execute(
                    select(tables.device_link).where(
                        tables.device_link.c.source_id == source_id,
                        tables.device_link.c.external_key == serial,
                        tables.device_link.c.valid_to.is_(None),
                    )
                )
                .mappings()
                .first()
            )
            if current and (
                current["status"],
                current["ms_machine_id"],
                current["ms_customer_id"],
            ) == (status, machine, customer):
                link_id = current["id"]
            else:
                if current:
                    self.connection.execute(
                        update(tables.device_link)
                        .where(tables.device_link.c.id == current["id"])
                        .values(valid_to=utcnow())
                    )
                link_id = self.add(
                    tables.device_link,
                    source_id=source_id,
                    external_key=serial,
                    serial=serial,
                    ms_machine_id=machine,
                    ms_customer_id=customer,
                    status=status,
                    valid_from=utcnow(),
                )
            self.connection.execute(
                update(tables.record)
                .where(
                    tables.record.c.source_id == source_id,
                    tables.record.c.serial == serial,
                    unresolved,
                )
                .values(device_link_id=link_id)
            )
