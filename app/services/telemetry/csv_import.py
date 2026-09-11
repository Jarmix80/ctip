"""Import katalogów roboczych i archiwów, bez powielania oryginałów."""

import hashlib
import logging
import os
import time
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select, update

from app.models import telemetry as tables
from app.services.telemetry.parsers import PARSER_VERSION, parse_csv
from app.services.telemetry.store import TelemetryStore

logger = logging.getLogger(__name__)


def archive_directory(directory: Path, *, root=False) -> Path:
    """Wykorzystuje istniejący katalog bez różnic wynikających z wielkości liter."""
    candidates = [path for path in directory.iterdir() if path.name.casefold() == "archiwum"]
    if len(candidates) > 1:
        raise ValueError("archive_directory_ambiguous")
    target = candidates[0] if candidates else directory / ("Archiwum" if root else "archiwum")
    if target.is_symlink() or target.resolve().parent != directory.resolve():
        raise ValueError("archive_directory_unsafe")
    if target.exists() and not target.is_dir():
        raise ValueError("archive_directory_invalid")
    return target


def archive_committed(engine, marker: dict):
    """Przenosi tylko zatwierdzony oryginał; identyczne archiwum wykorzystuje ponownie."""
    if marker["archive_status"] != "pending":
        return
    path = Path(marker["locator"])
    digest = marker["fingerprint"].split(":", 1)[0]
    directory = archive_directory(path.parent, root=bool(marker.get("root_archive")))
    directory.mkdir(exist_ok=True)
    target = Path(marker["archive_path"]) if marker.get("archive_path") else directory / path.name
    if target.parent.resolve() != directory.resolve() or target.is_symlink():
        raise ValueError("archive_target_unsafe")
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        if marker.get("archive_path"):
            raise ValueError("archive_collision")
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
        if target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ValueError("archive_collision")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("archive_source_changed")
        path.unlink()
    elif not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        raise ValueError("archive_missing")
    with engine.begin() as connection:
        connection.execute(
            update(tables.origin)
            .where(tables.origin.c.id == marker["id"])
            .values(archive_status="archived")
        )


def run_csv(runner):
    """Czyta również archiwa; pliki wejściowe usuwa dopiero po trwałym zapisie."""
    prepared = []
    for name, directory, pattern, archive in runner.csv_jobs():
        source = runner.source(name, "csv")
        try:
            if not directory.is_dir() or directory.is_symlink():
                raise ValueError("csv_directory_missing")
            target = archive_directory(directory, root=name == "remote_dplac")
            if archive and not runner.dry_run:
                target.mkdir(exist_ok=True)
                with runner.engine.connect() as connection:
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
                        archive_committed(
                            runner.engine, {**marker, "root_archive": name == "remote_dplac"}
                        )
                    except Exception as error:
                        runner.failure(source, marker["locator"], error)
            paths = [
                (path, archive)
                for path in sorted(directory.iterdir())
                if path.suffix.casefold() == ".csv"
                and (pattern == "*.csv" or path.name.upper().startswith("DPLAC"))
            ]
            if target.is_dir():
                paths.extend(
                    (path, False)
                    for path in sorted(target.rglob("*"))
                    if path.suffix.casefold() == ".csv"
                )
            for path, move in paths:
                if path.is_symlink() or not path.is_file():
                    continue
                if any(
                    parent.is_symlink() for parent in path.parents if parent != directory.parent
                ):
                    raise ValueError("csv_path_unsafe")
                stat = path.stat()
                prepared.append((source, path, move, (stat.st_size, stat.st_mtime_ns), target))
        except Exception as error:
            runner.failure(source, str(directory), error)
    if prepared and not runner.dry_run:
        time.sleep(runner.config.stability_seconds)
    processed = 0
    for source, path, move, signature, _target in prepared:
        if runner.item_limit is not None and processed >= runner.item_limit:
            break
        try:
            if not path.exists():
                continue
            stat = path.stat()
            if (stat.st_size, stat.st_mtime_ns) != signature:
                raise ValueError("csv_still_writing")
            if stat.st_size > runner.config.max_file_bytes:
                raise ValueError("csv_size")
            blob = path.read_bytes()
            stat = path.stat()
            if (stat.st_size, stat.st_mtime_ns) != signature or len(blob) != signature[0]:
                raise ValueError("csv_changed_during_read")
            digest = hashlib.sha256(blob).hexdigest()
            marker = None
            if not runner.dry_run:
                with runner.engine.begin() as connection:
                    store = TelemetryStore(connection)
                    marker = store.file_marker(source["id"], str(path), digest)
                    if marker is None:
                        marker = (
                            connection.execute(
                                select(tables.origin).where(
                                    tables.origin.c.source_id == source["id"],
                                    tables.origin.c.archive_path == str(path),
                                    tables.origin.c.position == "__artifact__",
                                    tables.origin.c.fingerprint == digest + ":" + PARSER_VERSION,
                                )
                            )
                            .mappings()
                            .first()
                        )
                    if marker:
                        marker = store.ingest(
                            source["id"], str(path), [], blob=blob, embedded=True
                        )["marker"]
            if marker is None:
                readings = parse_csv(blob, runner.config.csv_timezone)
                result = runner.save(
                    source,
                    str(path),
                    readings,
                    blob=blob,
                    media_type="text/csv",
                    embedded=True,
                    archive=move,
                )
                marker = result.get("marker")
            else:
                summary = runner.summary[source["name"]]
                summary["unchanged_files"] = summary.get("unchanged_files", 0) + 1
            if move and not runner.dry_run:
                with runner.engine.begin() as connection:
                    if marker["archive_status"] != "pending":
                        connection.execute(
                            update(tables.origin)
                            .where(tables.origin.c.id == marker["id"])
                            .values(archive_status="pending", archive_path=None)
                        )
                        marker = {**marker, "archive_status": "pending", "archive_path": None}
                archive_committed(
                    runner.engine, {**marker, "root_archive": source["name"] == "remote_dplac"}
                )
            processed += 1
            logger.info("CSV %s: zakończono plik %s/%s", source["name"], processed, len(prepared))
        except Exception as error:
            runner.failure(source, str(path), error)
