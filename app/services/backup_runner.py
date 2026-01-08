"""Pomocnicze funkcje dla modułu kopii zapasowych (MVP)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

BACKUP_DIR = Path("backups")


@dataclass(slots=True)
class BackupFileInfo:
    """Opis pojedynczego pliku kopii zapasowej na dysku."""

    name: str
    size_bytes: int
    modified_at: datetime
    status: str
    checksum: str | None = None


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
        stat = path.stat()
        modified_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
        items.append(
            BackupFileInfo(
                name=path.name,
                size_bytes=stat.st_size,
                modified_at=modified_at,
                status="READY",
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


__all__ = ["BACKUP_DIR", "BackupFileInfo", "format_backup_size", "list_backup_files"]
