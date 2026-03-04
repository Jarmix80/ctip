"""Pomocnicze funkcje dla modułu kopii zapasowych."""

from __future__ import annotations

import hashlib
import io
import json
import re
import tarfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import settings

BACKUP_DIR = Path("backups")


@dataclass(slots=True)
class BackupFileInfo:
    """Opis pojedynczego pliku kopii zapasowej na dysku."""

    name: str
    size_bytes: int
    modified_at: datetime
    status: str
    checksum: str | None = None


@dataclass(slots=True)
class BackupRunResult:
    """Wynik utworzenia lokalnej kopii zapasowej."""

    backup_name: str
    backup_path: Path
    checksum: str
    checksum_path: Path
    size_bytes: int
    notes: list[str]


class BackupRunError(RuntimeError):
    """Błąd wykonania kopii zapasowej."""


def list_backup_files(limit: int = 200) -> list[BackupFileInfo]:
    """Zwraca listę plików kopii zapasowych znalezionych w katalogu BACKUP_DIR."""
    if limit <= 0:
        return []
    if not BACKUP_DIR.exists():
        return []

    items: list[BackupFileInfo] = []
    for path in BACKUP_DIR.iterdir():
        if not path.is_file():
            continue
        if path.name.startswith("."):
            continue
        if path.suffix.lower() == ".sha256":
            continue
        stat = path.stat()
        modified_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
        checksum = None
        sidecar = path.with_name(f"{path.name}.sha256")
        if sidecar.exists() and sidecar.is_file():
            raw = sidecar.read_text(encoding="utf-8", errors="ignore").strip()
            checksum = raw.split()[0] if raw else None
        status = "CONFIRMED" if checksum else "READY"
        items.append(
            BackupFileInfo(
                name=path.name,
                size_bytes=stat.st_size,
                modified_at=modified_at,
                status=status,
                checksum=checksum,
            )
        )

    items.sort(key=lambda item: item.modified_at, reverse=True)
    return items[:limit]


def format_backup_size(size_bytes: int | None) -> str:
    """Formatuje rozmiar kopii w czytelnej postaci."""
    if size_bytes is None:
        return "—"
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(size_bytes)
    idx = 0
    while size >= 1024 and idx < len(units) - 1:
        size /= 1024
        idx += 1
    if idx == 0:
        return f"{int(size)} {units[idx]}"
    return f"{size:.1f} {units[idx]}"


def _slugify_label(label: str | None) -> str:
    if not label:
        return ""
    normalized = re.sub(r"[^0-9A-Za-z_-]+", "_", label.strip())
    normalized = normalized.strip("_")
    if not normalized:
        return ""
    return f"_{normalized[:24]}"


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _add_if_exists(archive: tarfile.TarFile, src: Path, arcname: str, notes: list[str]) -> None:
    if not src.exists():
        notes.append(f"Pominięto brakujący zasób: {src}")
        return
    archive.add(src, arcname=arcname, recursive=True)


def create_local_backup(
    *,
    label: str | None,
    compress: bool,
    config: dict[str, Any],
    project_root: Path | None = None,
) -> BackupRunResult:
    """Tworzy lokalne archiwum backupu i sumę kontrolną."""
    root = (project_root or Path.cwd()).resolve()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    suffix = ".tar.gz" if compress else ".tar"
    backup_name = f"backup_{stamp}{_slugify_label(label)}{suffix}"
    backup_path = BACKUP_DIR / backup_name
    mode = "w:gz" if compress else "w"
    notes: list[str] = []

    manifest = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "label": label,
        "compress": compress,
        "config": {
            "archive_ctip_files": bool(config.get("archive_ctip_files", True)),
            "archive_ctip_db": bool(config.get("archive_ctip_db", True)),
            "archive_firebird_prod": bool(config.get("archive_firebird_prod", True)),
            "archive_firebird_test": bool(config.get("archive_firebird_test", True)),
            "archive_optima": bool(config.get("archive_optima", True)),
        },
        "notes": notes,
    }

    try:
        with tarfile.open(backup_path, mode) as archive:
            if bool(config.get("archive_ctip_files", True)):
                _add_if_exists(archive, root / "app", "ctip/app", notes)
                _add_if_exists(archive, root / "scripts", "ctip/scripts", notes)
                _add_if_exists(archive, root / "README.md", "ctip/README.md", notes)

            if bool(config.get("archive_firebird_prod", True)):
                fb_prod = Path(settings.fb_database)
                _add_if_exists(archive, fb_prod, "firebird/prod/BAZAMS.FDB", notes)

            if bool(config.get("archive_firebird_test", True)):
                fb_test = Path(settings.fb_local_copy_path)
                _add_if_exists(archive, fb_test, "firebird/test/menadzer_serwisu.fdb", notes)

            if bool(config.get("archive_ctip_db", True)):
                notes.append("Archiwizacja PostgreSQL wymaga pg_dump - pominięto w tym przebiegu.")
            if bool(config.get("archive_optima", True)):
                notes.append(
                    "Archiwizacja SQL Optimy wymaga dumpa SQL Server - pominięto w tym przebiegu."
                )

            manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
            info = tarfile.TarInfo(name="manifest.json")
            info.size = len(manifest_bytes)
            info.mtime = int(datetime.now(UTC).timestamp())
            archive.addfile(info, io.BytesIO(manifest_bytes))
    except OSError as exc:
        raise BackupRunError(f"Nie udało się utworzyć archiwum backupu: {exc}") from exc

    checksum = _sha256_file(backup_path)
    checksum_path = backup_path.with_name(f"{backup_path.name}.sha256")
    checksum_path.write_text(f"{checksum}  {backup_path.name}\n", encoding="utf-8")

    size_bytes = backup_path.stat().st_size
    return BackupRunResult(
        backup_name=backup_name,
        backup_path=backup_path,
        checksum=checksum,
        checksum_path=checksum_path,
        size_bytes=size_bytes,
        notes=notes,
    )


__all__ = [
    "BACKUP_DIR",
    "BackupFileInfo",
    "BackupRunError",
    "BackupRunResult",
    "create_local_backup",
    "format_backup_size",
    "list_backup_files",
]
