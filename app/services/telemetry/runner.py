"""Wznawialny import źródeł oraz archiwizacja wyłącznie zatwierdzonych raportów."""

import copy
import hashlib
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select, update

from app.models import telemetry as tables
from app.services.telemetry import sources
from app.services.telemetry.parsers import Reading, parse_csv, parse_mail, serial_number
from app.services.telemetry.store import TelemetryStore

logger = logging.getLogger(__name__)


def file_signature(path: Path):
    """Pobiera parametry stabilności bez modyfikowania pliku."""
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def archive_committed(engine, marker: dict):
    """Przenosi zatwierdzony plik przez wyłączne utworzenie dowiązania na tym samym woluminie."""
    if marker["archive_status"] != "pending":
        return
    path = Path(marker["locator"])
    digest = marker["fingerprint"].split(":", 1)[0]
    directory = path.parent / "archiwum"
    if path.name.upper().startswith("DPLAC"):
        raise ValueError("dplac_archive_forbidden")
    if directory.is_symlink() or directory.resolve().parent != path.parent.resolve():
        raise ValueError("archive_directory_unsafe")
    directory.mkdir(exist_ok=True)
    target = Path(marker["archive_path"]) if marker.get("archive_path") else directory / path.name
    if target.parent.resolve() != directory.resolve():
        raise ValueError("archive_target_unsafe")
    if target.exists() and not marker.get("archive_path"):
        target = directory / f"{path.stem}_{digest[:12]}_{uuid4().hex[:8]}{path.suffix}"
    with engine.begin() as connection:
        connection.execute(
            update(tables.origin)
            .where(tables.origin.c.id == marker["id"])
            .values(archive_path=str(target))
        )
    if path.exists():
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("archive_source_changed")
        if not target.exists():
            os.link(path, target)
        if not os.path.samefile(path, target):
            raise ValueError("archive_collision")
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ValueError("archive_content_changed")
        path.unlink()
    elif not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        raise ValueError("archive_missing")
    with engine.begin() as connection:
        connection.execute(
            update(tables.origin)
            .where(tables.origin.c.id == marker["id"])
            .values(archive_status="archived")
        )


class ImportRunner:
    """Koordynuje źródła; awaria jednego nie zatrzymuje pozostałych."""

    def __init__(self, config, engine=None, *, dry_run=False, backfill=False):
        self.config = config
        self.engine = engine
        self.dry_run = dry_run
        self.backfill = backfill
        self.summary = {}

    def source(self, name, kind):
        """Otwiera rejestr źródła albo jego nietrwały odpowiednik kontrolny."""
        self.summary.setdefault(name, {})
        if self.dry_run:
            return {"id": name, "name": name, "kind": kind, "checkpoint": {}}
        with self.engine.begin() as connection:
            return TelemetryStore(connection).ensure_source(name, kind)

    def failure(self, source, locator, error):
        """Zapisuje wyłącznie bezpieczny typ błędu; poświadczenia nie trafiają do logów."""
        code = (
            str(error)
            if isinstance(error, ValueError) and str(error).replace("_", "").isalnum()
            else type(error).__name__
        )
        self.summary.setdefault(source["name"], {}).setdefault("errors", []).append(code)
        logger.warning("Źródło %s: %s", source["name"], code)
        if not self.dry_run:
            with self.engine.begin() as connection:
                TelemetryStore(connection).failure(source["id"], locator, code)

    def save(self, source, locator, readings, *, checkpoint=None, **kwargs):
        """Zatwierdza porcję i jej kursor razem; tryb kontrolny tylko zlicza rekordy."""
        if self.dry_run:
            result = {"new": len(readings), "duplicates": 0}
        else:
            with self.engine.begin() as connection:
                store = TelemetryStore(connection)
                result = store.ingest(source["id"], locator, readings, **kwargs)
                if checkpoint is not None:
                    store.checkpoint(source["id"], checkpoint)
        summary = self.summary.setdefault(source["name"], {})
        for key in ("new", "duplicates"):
            summary[key] = summary.get(key, 0) + result[key]
        return result

    def csv_jobs(self):
        """Rozdziela pozostawiane DPLAC od trzech archiwizowanych katalogów raportów."""
        jobs = []
        if self.config.dplac_root:
            jobs.append(("remote_dplac", Path(self.config.dplac_root), "DPLAC*.csv", False))
        if self.config.report_root:
            for name in ("Toner", "All Supplies", "Reporting"):
                jobs.append(
                    (
                        "remote_" + name.lower().replace(" ", "_"),
                        Path(self.config.report_root) / name,
                        "*.csv",
                        True,
                    )
                )
        return jobs

    def run_csv(self):
        """Sprawdza stabilność wszystkich plików równolegle w jednym oknie czasowym."""
        prepared = []
        for name, directory, pattern, archive in self.csv_jobs():
            source = self.source(name, "csv")
            try:
                if not directory.is_dir():
                    raise ValueError("csv_directory_missing")
                if archive and not self.dry_run:
                    target = directory / "archiwum"
                    if target.is_symlink() or target.resolve().parent != directory.resolve():
                        raise ValueError("archive_directory_unsafe")
                    target.mkdir(exist_ok=True)
                files = [
                    (path, file_signature(path))
                    for path in sorted(directory.glob(pattern))
                    if path.is_file()
                    and not path.is_symlink()
                    and path.resolve().parent == directory.resolve()
                ]
                prepared.append((source, files, archive))
            except Exception as error:
                self.failure(source, str(directory), error)
        if any(files for _, files, _ in prepared) and not self.dry_run:
            time.sleep(self.config.stability_seconds)
        for source, files, archive in prepared:
            if archive and not self.dry_run:
                with self.engine.connect() as connection:
                    pending = (
                        connection.execute(
                            select(tables.origin).where(
                                tables.origin.c.source_id == source["id"],
                                tables.origin.c.archive_status == "pending",
                            )
                        )
                        .mappings()
                        .all()
                    )
                for marker in pending:
                    try:
                        archive_committed(self.engine, dict(marker))
                    except Exception as error:
                        self.failure(source, marker["locator"], error)
            for path, signature in files:
                try:
                    if not path.exists():
                        continue
                    if signature != file_signature(path):
                        raise ValueError("csv_still_writing")
                    if signature[0] > self.config.max_file_bytes:
                        raise ValueError("csv_size")
                    blob = path.read_bytes()
                    if signature != file_signature(path) or len(blob) != signature[0]:
                        raise ValueError("csv_changed_during_read")
                    digest = hashlib.sha256(blob).hexdigest()
                    if not self.dry_run:
                        with self.engine.connect() as connection:
                            marker = TelemetryStore(connection).file_marker(
                                source["id"], str(path), digest
                            )
                        if marker:
                            if archive:
                                if marker["archive_status"] == "archived":
                                    marker = {
                                        **marker,
                                        "archive_status": "pending",
                                        "archive_path": None,
                                    }
                                    with self.engine.begin() as connection:
                                        connection.execute(
                                            update(tables.origin)
                                            .where(tables.origin.c.id == marker["id"])
                                            .values(archive_status="pending", archive_path=None)
                                        )
                                archive_committed(self.engine, dict(marker))
                            self.summary.setdefault(source["name"], {}).setdefault(
                                "unchanged_files", 0
                            )
                            self.summary[source["name"]]["unchanged_files"] += 1
                            continue
                    readings = parse_csv(blob, self.config.csv_timezone)
                    result = self.save(
                        source,
                        str(path),
                        readings,
                        blob=blob,
                        media_type="text/csv",
                        embedded=archive,
                        archive=archive,
                    )
                    if archive and not self.dry_run:
                        archive_committed(self.engine, result["marker"])
                except Exception as error:
                    self.failure(source, str(path), error)

    def run_mail(self):
        """Pobiera wiadomości przyrostowo, ponawiając błędy niezależnie od nowych UID."""
        source = self.source("remote_mail", "imap")
        checkpoint = copy.deepcopy(source["checkpoint"])
        if self.backfill:
            checkpoint = {}
        with sources.remote_mailbox(self.config) as mailbox:
            generation, new_uids = sources.mail_uids(mailbox, checkpoint)
            retry = (
                checkpoint.get("retry", []) if checkpoint.get("uidvalidity") == generation else []
            )
            selected = sorted(set(retry + new_uids))
            if not self.backfill:
                selected = selected[: self.config.page_size * self.config.max_pages]
            batch = {}
            for position, uid in enumerate(selected):
                locator = f"{self.config.imap_folder}/{generation}/{uid}"
                try:
                    if position % self.config.page_size == 0:
                        batch = {}
                        batch = sources.mail_batch(
                            mailbox,
                            selected[position : position + self.config.page_size],
                            self.config.max_file_bytes,
                        )
                    if uid not in batch:
                        raise ValueError("imap_message_missing_or_oversized")
                    blob = batch.pop(uid)
                    try:
                        readings = parse_mail(blob, self.config.mail_timezone)
                    except Exception:
                        readings = [
                            Reading(
                                locator,
                                "unparsed_mail",
                                "",
                                {"error": "mail_parse"},
                                issues=[("mail_parse", "")],
                            )
                        ]
                    checkpoint["uidvalidity"] = generation
                    checkpoint["uid"] = max(uid, int(checkpoint.get("uid", 0)))
                    checkpoint["retry"] = [value for value in retry if value != uid]
                    self.save(
                        source,
                        locator,
                        readings,
                        blob=blob,
                        media_type="message/rfc822",
                        embedded=True,
                        checkpoint=checkpoint,
                    )
                    retry = checkpoint["retry"]
                except Exception as error:
                    if uid not in retry:
                        retry.append(uid)
                    checkpoint["retry"] = retry
                    self.failure(source, locator, error)
            if not self.dry_run:
                with self.engine.begin() as connection:
                    TelemetryStore(connection).checkpoint(source["id"], checkpoint)

    def run_database(self, kind):
        """Łączy przyrostowe kursory z codziennym porcjowanym przeglądem starszych wersji."""
        source = self.source(kind, "database")
        checkpoint = copy.deepcopy(source["checkpoint"])
        today = datetime.now(UTC).date().isoformat()
        if kind == "vmaintenance":
            identities = sources.vm_serials(self.config)
            tables_map = sources.VM_TABLES
        else:
            identities = {}
            after = None
            while True:
                rows = sources.printradar_page(
                    self.config.printradar_dsn.get_secret_value(),
                    "device_fingerprints",
                    after,
                    self.config.page_size,
                )
                if not rows:
                    break
                identities.update(
                    {
                        row["fingerprint_key"]: serial_number(row.get("serial_number"))
                        for row in rows
                    }
                )
                after = rows[-1]["fingerprint_key"]
            tables_map = sources.PR_TABLES
        for table, primary in tables_map.items():
            state = checkpoint.setdefault(table, {})
            mutable = table in {
                "MASZYNY",
                "WEZWANIE",
                "MAGAZYNY",
                "DODAJ",
                "CPC",
                "device_fingerprints",
            }
            full = self.backfill or mutable or state.get("reconciled") != today
            after = state.get("scan") if full else state.get("watermark")
            pages = 0
            try:
                while self.backfill or pages < self.config.max_pages:
                    if kind == "vmaintenance":
                        rows = sources.vm_page(table, after, self.config.page_size, self.config)
                        readings = [sources.vm_reading(table, row, identities) for row in rows]
                    else:
                        rows = sources.printradar_page(
                            self.config.printradar_dsn.get_secret_value(),
                            table,
                            after,
                            self.config.page_size,
                            incremental=not full,
                        )
                        readings = [
                            sources.printradar_reading(table, row, identities) for row in rows
                        ]
                    candidate = copy.deepcopy(checkpoint)
                    state = candidate.setdefault(table, {})
                    if rows:
                        watermark = (
                            max(row[primary] for row in rows)
                            if kind == "vmaintenance" or mutable
                            else max([row["created_at"], row[primary]] for row in rows)
                        )
                        if state.get("watermark") is None or watermark > state["watermark"]:
                            state["watermark"] = watermark
                        after = (
                            rows[-1][primary]
                            if full or kind == "vmaintenance"
                            else [rows[-1]["created_at"], rows[-1][primary]]
                        )
                    if full:
                        state["scan"] = after
                    if len(rows) < self.config.page_size:
                        state["scan"] = None
                        state["reconciled"] = today
                    self.save(source, f"{table}/{after}", readings, checkpoint=candidate)
                    checkpoint = candidate
                    pages += 1
                    if len(rows) < self.config.page_size:
                        break
            except Exception as error:
                self.failure(source, table, error)

    def run(self):
        """Uruchamia wyłącznie skonfigurowane źródła oraz opcjonalne dopasowanie MS."""
        if not self.dry_run:
            with self.engine.begin() as connection:
                connection.execute(update(tables.source).values(enabled=False))
        self.run_csv()
        for enabled, name, callback in (
            (self.config.mail_enabled, "remote_mail", self.run_mail),
            (self.config.vm_enabled, "vmaintenance", lambda: self.run_database("vmaintenance")),
            (
                bool(self.config.printradar_dsn.get_secret_value()),
                "printradar",
                lambda: self.run_database("printradar"),
            ),
        ):
            if enabled:
                try:
                    callback()
                except Exception as error:
                    self.failure(
                        self.source(name, "imap" if name == "remote_mail" else "database"),
                        name,
                        error,
                    )
        if self.config.ms_enabled:
            source = self.source("ms_identity", "database")
            try:
                mapping = sources.ms_identity_map(self.config)
                if not self.dry_run:
                    with self.engine.begin() as connection:
                        store = TelemetryStore(connection)
                        identifiers = connection.execute(select(tables.source.c.id)).scalars().all()
                        for identifier in identifiers:
                            store.bind_devices(identifier, mapping)
                        store.checkpoint(source["id"], {"serials": len(mapping)})
                self.summary["ms_identity"] = {"serials": len(mapping)}
            except Exception as error:
                self.failure(source, "MASZYNA", error)
        if not self.dry_run:
            with self.engine.begin() as connection:
                for name, summary in self.summary.items():
                    values = (
                        {"last_error": summary["errors"][-1]}
                        if summary.get("errors")
                        else {"last_error": None, "last_success_at": datetime.now(UTC)}
                    )
                    connection.execute(
                        update(tables.source).where(tables.source.c.name == name).values(**values)
                    )
        return self.summary
