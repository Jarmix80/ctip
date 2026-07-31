"""Wspólny mechanizm czasowej retencji lokalnych i zdalnych kopii zapasowych."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

_MAIN_ARCHIVE_PATTERN = re.compile(
    r"^(?P<primary>backup_\d{8}_\d{6}(?:_[0-9A-Za-z_-]+)?\.tar(?:\.gz)?)(?P<checksum>\.sha256)?$"
)
_FIREBIRD_PATTERN = re.compile(
    r"^(?P<prefix>ctip_firebird_(?P<scope>prod|test)_(?P<stamp>\d{8}_\d{6}))"
    r"(?:(?P<primary>\.fbk)(?P<checksum>\.sha256)?|(?P<manifest>_manifest\.json))$"
)
_OPTIMA_PATTERN = re.compile(
    r"^(?P<prefix>ctip_optima_(?P<stamp>\d{8}_\d{6}))"
    r"(?:(?:_(?P<database>[0-9A-Za-z_-]+)(?P<primary>\.bak)(?P<checksum>\.sha256)?)"
    r"|(?P<manifest>_manifest\.json))$"
)


@dataclass(frozen=True, slots=True)
class RetentionItem:
    """Metadane pojedynczego pliku uwzględnianego przy obliczaniu retencji."""

    name: str
    modified_at: datetime
    size_bytes: int = 0
    identifier: str | None = None


@dataclass(frozen=True, slots=True)
class ManagedRetentionItem:
    """Plik rozpoznany jako element zarządzanego zestawu kopii."""

    item: RetentionItem
    category: str
    set_key: str
    role: str
    primary_name: str | None = None


@dataclass(slots=True)
class RetentionSet:
    """Zestaw plików należących do jednego przebiegu kopii zapasowej."""

    key: str
    category: str
    items: list[ManagedRetentionItem] = field(default_factory=list)

    @property
    def modified_at(self) -> datetime:
        """Zwraca najnowszą datę modyfikacji elementu zestawu."""
        return max(item.item.modified_at for item in self.items)

    @property
    def size_bytes(self) -> int:
        """Zwraca łączny rozmiar elementów zestawu."""
        return sum(max(item.item.size_bytes, 0) for item in self.items)

    @property
    def complete(self) -> bool:
        """Potwierdza obecność sumy kontrolnej dla każdego pliku głównego."""
        primary_names = {
            item.primary_name for item in self.items if item.role == "primary" and item.primary_name
        }
        checksum_names = {
            item.primary_name
            for item in self.items
            if item.role == "checksum" and item.primary_name
        }
        return bool(primary_names) and primary_names <= checksum_names

    def as_dict(self) -> dict[str, object]:
        """Zwraca bezpieczną reprezentację zestawu do API i dziennika audytowego."""
        return {
            "key": self.key,
            "category": self.category,
            "modified_at": self.modified_at.isoformat(),
            "complete": self.complete,
            "size_bytes": self.size_bytes,
            "files": sorted(item.item.name for item in self.items),
        }


@dataclass(slots=True)
class RetentionPlan:
    """Plan retencji przed wykonaniem operacji usuwania."""

    retention_days: int
    cutoff_at: datetime
    sets: list[RetentionSet]
    deletion_sets: list[RetentionSet]
    preserved_newest_key: str | None
    unknown_items: list[RetentionItem]
    newer_incomplete_sets: list[RetentionSet]

    @property
    def managed_files(self) -> int:
        """Zwraca liczbę rozpoznanych plików kopii."""
        return sum(len(item_set.items) for item_set in self.sets)

    @property
    def deletion_files(self) -> int:
        """Zwraca liczbę plików zakwalifikowanych do usunięcia."""
        return sum(len(item_set.items) for item_set in self.deletion_sets)

    @property
    def deletion_bytes(self) -> int:
        """Zwraca rozmiar plików zakwalifikowanych do usunięcia."""
        return sum(item_set.size_bytes for item_set in self.deletion_sets)

    def as_dict(self) -> dict[str, object]:
        """Zwraca plan w formacie przeznaczonym do API i audytu."""
        return {
            "retention_days": self.retention_days,
            "cutoff_at": self.cutoff_at.isoformat(),
            "managed_sets": len(self.sets),
            "managed_files": self.managed_files,
            "candidate_sets": len(self.deletion_sets),
            "candidate_files": self.deletion_files,
            "candidate_bytes": self.deletion_bytes,
            "preserved_newest_key": self.preserved_newest_key,
            "unknown_files": sorted(item.name for item in self.unknown_items),
            "newer_incomplete_sets": [
                item_set.as_dict() for item_set in self.newer_incomplete_sets
            ],
            "deletion_sets": [item_set.as_dict() for item_set in self.deletion_sets],
        }


@dataclass(slots=True)
class RetentionApplyResult:
    """Wynik zastosowania przygotowanego planu retencji."""

    dry_run: bool
    deleted_sets: int = 0
    deleted_files: int = 0
    deleted_bytes: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        """Zwraca wynik wykonania w formacie przeznaczonym do API i audytu."""
        return {
            "dry_run": self.dry_run,
            "deleted_sets": self.deleted_sets,
            "deleted_files": self.deleted_files,
            "deleted_bytes": self.deleted_bytes,
            "errors": self.errors,
        }


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def classify_retention_item(item: RetentionItem) -> ManagedRetentionItem | None:
    """Rozpoznaje nazwę zarządzanego pliku i przypisuje ją do zestawu."""
    main_match = _MAIN_ARCHIVE_PATTERN.fullmatch(item.name)
    if main_match:
        primary_name = main_match.group("primary")
        role = "checksum" if main_match.group("checksum") else "primary"
        return ManagedRetentionItem(
            item=item,
            category="ctip",
            set_key=f"ctip:{primary_name}",
            role=role,
            primary_name=primary_name,
        )

    firebird_match = _FIREBIRD_PATTERN.fullmatch(item.name)
    if firebird_match:
        prefix = firebird_match.group("prefix")
        primary_name = f"{prefix}.fbk"
        if firebird_match.group("manifest"):
            role = "manifest"
            primary_name = None
        elif firebird_match.group("checksum"):
            role = "checksum"
        else:
            role = "primary"
        scope = firebird_match.group("scope")
        return ManagedRetentionItem(
            item=item,
            category=f"firebird_{scope}",
            set_key=f"firebird_{scope}:{firebird_match.group('stamp')}",
            role=role,
            primary_name=primary_name,
        )

    optima_match = _OPTIMA_PATTERN.fullmatch(item.name)
    if optima_match:
        prefix = optima_match.group("prefix")
        database = optima_match.group("database")
        primary_name = f"{prefix}_{database}.bak" if database else None
        if optima_match.group("manifest"):
            role = "manifest"
        elif optima_match.group("checksum"):
            role = "checksum"
        else:
            role = "primary"
        return ManagedRetentionItem(
            item=item,
            category="optima",
            set_key=f"optima:{optima_match.group('stamp')}",
            role=role,
            primary_name=primary_name,
        )
    return None


def build_retention_plan(
    items: Iterable[RetentionItem],
    *,
    retention_days: int,
    now: datetime | None = None,
    preserve_newest_complete: bool = True,
) -> RetentionPlan:
    """Buduje czasowy plan retencji bez wykonywania operacji destrukcyjnych."""
    if retention_days < 1:
        raise ValueError("Retencja musi wynosić co najmniej jeden dzień.")

    reference_time = _as_utc(now or datetime.now(UTC))
    cutoff_at = reference_time - timedelta(days=retention_days)
    grouped: dict[str, RetentionSet] = {}
    unknown_items: list[RetentionItem] = []
    for raw_item in items:
        item = RetentionItem(
            name=raw_item.name,
            modified_at=_as_utc(raw_item.modified_at),
            size_bytes=raw_item.size_bytes,
            identifier=raw_item.identifier,
        )
        managed = classify_retention_item(item)
        if managed is None:
            unknown_items.append(item)
            continue
        item_set = grouped.setdefault(
            managed.set_key,
            RetentionSet(key=managed.set_key, category=managed.category),
        )
        item_set.items.append(managed)

    sets = sorted(grouped.values(), key=lambda item_set: item_set.modified_at, reverse=True)
    newest_complete = next((item_set for item_set in sets if item_set.complete), None)
    preserved_key = newest_complete.key if preserve_newest_complete and newest_complete else None
    deletion_sets = [
        item_set
        for item_set in sets
        if item_set.modified_at < cutoff_at and item_set.key != preserved_key
    ]
    newer_incomplete_sets = [
        item_set for item_set in sets if not item_set.complete and item_set.modified_at >= cutoff_at
    ]
    return RetentionPlan(
        retention_days=retention_days,
        cutoff_at=cutoff_at,
        sets=sets,
        deletion_sets=deletion_sets,
        preserved_newest_key=preserved_key,
        unknown_items=unknown_items,
        newer_incomplete_sets=newer_incomplete_sets,
    )


def list_local_retention_items(directory: Path) -> list[RetentionItem]:
    """Odczytuje metadane zwykłych plików z jednego katalogu lokalnego."""
    if not directory.exists():
        return []
    items: list[RetentionItem] = []
    for path in directory.iterdir():
        if path.is_symlink() or not path.is_file():
            continue
        stat = path.stat()
        items.append(
            RetentionItem(
                name=path.name,
                modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                size_bytes=stat.st_size,
                identifier=str(path),
            )
        )
    return items


def apply_local_retention(
    directory: Path,
    plan: RetentionPlan,
    *,
    dry_run: bool,
) -> RetentionApplyResult:
    """Stosuje plan retencji wyłącznie do plików bezpośrednio w podanym katalogu."""
    result = RetentionApplyResult(dry_run=dry_run)
    if dry_run:
        return result

    root = directory.resolve()
    for item_set in plan.deletion_sets:
        set_deleted_files = 0
        set_deleted_bytes = 0
        set_failed = False
        for managed in item_set.items:
            candidate = (directory / managed.item.name).resolve()
            if candidate.parent != root:
                result.errors.append(f"Odrzucono niebezpieczną ścieżkę: {managed.item.name}")
                set_failed = True
                continue
            try:
                candidate.unlink(missing_ok=True)
            except OSError as exc:
                result.errors.append(f"{managed.item.name}: {exc}")
                set_failed = True
                continue
            set_deleted_files += 1
            set_deleted_bytes += max(managed.item.size_bytes, 0)
        result.deleted_files += set_deleted_files
        result.deleted_bytes += set_deleted_bytes
        if not set_failed:
            result.deleted_sets += 1
    return result


def run_local_retention(
    directory: Path,
    *,
    retention_days: int,
    dry_run: bool,
    preserve_newest_complete: bool = True,
    now: datetime | None = None,
) -> tuple[RetentionPlan, RetentionApplyResult]:
    """Planuje i opcjonalnie wykonuje czasową retencję lokalnego katalogu."""
    plan = build_retention_plan(
        list_local_retention_items(directory),
        retention_days=retention_days,
        now=now,
        preserve_newest_complete=preserve_newest_complete,
    )
    result = apply_local_retention(directory, plan, dry_run=dry_run)
    return plan, result


__all__ = [
    "ManagedRetentionItem",
    "RetentionApplyResult",
    "RetentionItem",
    "RetentionPlan",
    "RetentionSet",
    "apply_local_retention",
    "build_retention_plan",
    "classify_retention_item",
    "list_local_retention_items",
    "run_local_retention",
]
