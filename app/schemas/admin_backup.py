"""Schematy Pydantic dla modułu kopii zapasowych."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class BackupHistoryEntry(BaseModel):
    """Pojedyncza pozycja historii kopii zapasowych."""

    name: str
    size_bytes: int | None
    modified_at: datetime | None
    status: str
    checksum: str | None = None


class BackupHistoryResponse(BaseModel):
    """Lista kopii zapasowych dostępnych w systemie."""

    generated_at: datetime
    items: list[BackupHistoryEntry]
    note: str | None = None


class BackupRunRequest(BaseModel):
    """Parametry uruchomienia zadania kopii zapasowej."""

    label: str | None = Field(default=None, max_length=120)
    compress: bool = True
    dry_run: bool = True


class BackupRunResponse(BaseModel):
    """Wynik inicjacji zadania kopii zapasowej."""

    accepted: bool
    dry_run: bool
    message: str
    backup_name: str | None = None


class BackupRestoreRequest(BaseModel):
    """Parametry przywrócenia kopii zapasowej."""

    backup_name: str = Field(..., min_length=1, max_length=240)
    dry_run: bool = True
    confirm: str | None = Field(default=None, max_length=24)


class BackupRestoreResponse(BaseModel):
    """Wynik inicjacji przywrócenia kopii zapasowej."""

    accepted: bool
    dry_run: bool
    message: str
