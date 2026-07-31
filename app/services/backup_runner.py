"""Pomocnicze funkcje dla modułu kopii zapasowych."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass, field
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
    included_components: list[str] = field(default_factory=list)
    omitted_components: list[str] = field(default_factory=list)
    postgres_dump_included: bool = False


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


def _add_if_exists(
    archive: tarfile.TarFile,
    src: Path,
    arcname: str,
    notes: list[str],
) -> bool:
    if not src.exists():
        notes.append(f"Pominięto brakujący zasób: {src}")
        return False
    archive.add(src, arcname=arcname, recursive=True)
    return True


def _resolve_postgres_tool(tool_name: str, configured_path: str | None) -> str:
    """Wyszukuje narzędzie PostgreSQL w konfiguracji, PATH i typowych katalogach Windows."""
    configured = (configured_path or "").strip().strip('"')
    if configured:
        configured_file = Path(configured)
        if configured_file.is_file():
            return str(configured_file)
        raise BackupRunError(f"Skonfigurowane narzędzie {tool_name} nie istnieje: {configured}")

    executable_names = [tool_name]
    if os.name == "nt" and not tool_name.lower().endswith(".exe"):
        executable_names.insert(0, f"{tool_name}.exe")
    for executable_name in executable_names:
        resolved = shutil.which(executable_name)
        if resolved:
            return resolved

    if os.name == "nt":
        candidates: list[Path] = []
        for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
            program_files = os.environ.get(env_name)
            if program_files:
                candidates.extend(Path(program_files).glob(f"PostgreSQL/*/bin/{tool_name}.exe"))
        if candidates:

            def version_key(candidate: Path) -> tuple[int, ...]:
                raw_version = candidate.parent.parent.name
                return tuple(int(part) for part in raw_version.split(".") if part.isdigit())

            return str(max(candidates, key=version_key))

    raise BackupRunError(
        f"Nie znaleziono narzędzia {tool_name}. Ustaw odpowiednią ścieżkę w pliku .env."
    )


def _process_error(result: subprocess.CompletedProcess[str]) -> str:
    """Zwraca skrócony komunikat procesu bez ujawniania danych połączeniowych."""
    message = (result.stderr or result.stdout or "brak szczegółów").strip()
    return message[-1000:]


def _create_postgres_dump(target_path: Path) -> None:
    """Tworzy logiczną kopię PostgreSQL i weryfikuje ją przez pg_restore --list."""
    pg_dump = _resolve_postgres_tool("pg_dump", settings.pg_dump_path)
    pg_restore_config = settings.pg_restore_path
    if not pg_restore_config:
        sibling_name = "pg_restore.exe" if Path(pg_dump).suffix.lower() == ".exe" else "pg_restore"
        sibling_path = Path(pg_dump).with_name(sibling_name)
        if sibling_path.is_file():
            pg_restore_config = str(sibling_path)
    pg_restore = _resolve_postgres_tool("pg_restore", pg_restore_config)

    process_env = os.environ.copy()
    process_env["PGPASSWORD"] = settings.pg_password
    process_env["PGSSLMODE"] = settings.pg_sslmode
    command = [
        pg_dump,
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        "--host",
        settings.pg_host,
        "--port",
        str(settings.pg_port),
        "--username",
        settings.pg_user,
        "--dbname",
        settings.pg_database,
        "--file",
        str(target_path),
    ]
    try:
        dump_result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=process_env,
            timeout=settings.backup_pg_dump_timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BackupRunError(f"Nie udało się uruchomić pg_dump: {exc}") from exc
    if dump_result.returncode != 0:
        raise BackupRunError(f"pg_dump zakończył się błędem: {_process_error(dump_result)}")
    if not target_path.is_file() or target_path.stat().st_size == 0:
        raise BackupRunError("pg_dump nie utworzył poprawnego pliku kopii PostgreSQL.")

    try:
        verify_result = subprocess.run(
            [pg_restore, "--list", str(target_path)],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=process_env,
            timeout=settings.backup_pg_dump_timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BackupRunError(f"Nie udało się zweryfikować kopii PostgreSQL: {exc}") from exc
    if verify_result.returncode != 0 or not verify_result.stdout.strip():
        raise BackupRunError(
            f"Walidacja pg_restore --list nie powiodła się: {_process_error(verify_result)}"
        )


def prune_local_backups(retention_count: int) -> int:
    """Usuwa lokalne archiwa przekraczające skonfigurowaną retencję."""
    if retention_count < 1 or not BACKUP_DIR.exists():
        return 0
    archives = [
        path
        for path in BACKUP_DIR.iterdir()
        if path.is_file()
        and path.name.startswith("backup_")
        and (path.name.endswith(".tar") or path.name.endswith(".tar.gz"))
    ]
    archives.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    deleted = 0
    for archive_path in archives[retention_count:]:
        checksum_path = archive_path.with_name(f"{archive_path.name}.sha256")
        archive_path.unlink(missing_ok=True)
        checksum_path.unlink(missing_ok=True)
        deleted += 1
    return deleted


def create_local_backup(
    *,
    label: str | None,
    compress: bool,
    config: dict[str, Any],
    project_root: Path | None = None,
    component_manifests: dict[str, list[Path]] | None = None,
) -> BackupRunResult:
    """Tworzy archiwum CTIP, dump PostgreSQL i odwołania do kopii zewnętrznych."""
    root = (project_root or Path.cwd()).resolve()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    suffix = ".tar.gz" if compress else ".tar"
    backup_name = f"backup_{stamp}{_slugify_label(label)}{suffix}"
    backup_path = BACKUP_DIR / backup_name
    mode = "w:gz" if compress else "w"
    notes: list[str] = []
    included_components: list[str] = []
    omitted_components: list[str] = []
    postgres_dump_included = False
    component_manifests = component_manifests or {}

    manifest = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "label": label,
        "compress": compress,
        "config": {
            "archive_ctip_files": bool(config.get("archive_ctip_files", True)),
            "archive_ctip_db": bool(config.get("archive_ctip_db", True)),
            "archive_firebird_prod": bool(config.get("archive_firebird_prod", True)),
            "archive_firebird_test": bool(config.get("archive_firebird_test", False)),
            "archive_optima": bool(config.get("archive_optima", True)),
        },
        "included_components": included_components,
        "omitted_components": omitted_components,
        "external_component_manifests": {},
        "notes": notes,
    }

    staging_parent = BACKUP_DIR.resolve()
    try:
        with tempfile.TemporaryDirectory(prefix=".ctip_backup_", dir=staging_parent) as temp_dir:
            postgres_dump_path: Path | None = None
            if bool(config.get("archive_ctip_db", True)):
                postgres_dump_path = Path(temp_dir) / "ctip.dump"
                _create_postgres_dump(postgres_dump_path)
                postgres_dump_included = True
                included_components.append("postgresql_ctip")

            with tarfile.open(backup_path, mode) as archive:
                if bool(config.get("archive_ctip_files", True)):
                    ctip_files_added = all(
                        (
                            _add_if_exists(archive, root / "app", "ctip/app", notes),
                            _add_if_exists(archive, root / "scripts", "ctip/scripts", notes),
                            _add_if_exists(archive, root / "README.md", "ctip/README.md", notes),
                        )
                    )
                    if ctip_files_added:
                        included_components.append("ctip_files")
                    else:
                        omitted_components.append("ctip_files")

                if postgres_dump_path is not None:
                    archive.add(postgres_dump_path, arcname="postgresql/ctip.dump")

                external_components = (
                    ("firebird_prod", "firebird/prod", "archive_firebird_prod", True),
                    ("firebird_test", "firebird/test", "archive_firebird_test", False),
                    ("optima_sql", "optima", "archive_optima", True),
                )
                manifest_references = manifest["external_component_manifests"]
                if not isinstance(manifest_references, dict):
                    raise BackupRunError("Niepoprawna struktura manifestu kopii zapasowej.")
                for (
                    component,
                    archive_directory,
                    config_key,
                    default_enabled,
                ) in external_components:
                    if not bool(config.get(config_key, default_enabled)):
                        continue
                    references = component_manifests.get(component, [])
                    if not references or any(not path.is_file() for path in references):
                        omitted_components.append(component)
                        notes.append(
                            f"Pominięto komponent {component}: brak zweryfikowanego manifestu."
                        )
                        continue
                    for reference in references:
                        archive.add(
                            reference,
                            arcname=f"external/{archive_directory}/{reference.name}",
                        )
                    manifest_references[component] = [reference.name for reference in references]
                    included_components.append(component)

                manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
                info = tarfile.TarInfo(name="manifest.json")
                info.size = len(manifest_bytes)
                info.mtime = int(datetime.now(UTC).timestamp())
                archive.addfile(info, io.BytesIO(manifest_bytes))
    except BackupRunError:
        backup_path.unlink(missing_ok=True)
        raise
    except (OSError, subprocess.SubprocessError, tarfile.TarError) as exc:
        backup_path.unlink(missing_ok=True)
        raise BackupRunError(f"Nie udało się utworzyć archiwum backupu: {exc}") from exc

    checksum_path = backup_path.with_name(f"{backup_path.name}.sha256")
    try:
        checksum = _sha256_file(backup_path)
        checksum_path.write_text(f"{checksum}  {backup_path.name}\n", encoding="utf-8")
        size_bytes = backup_path.stat().st_size
    except OSError as exc:
        backup_path.unlink(missing_ok=True)
        checksum_path.unlink(missing_ok=True)
        raise BackupRunError(f"Nie udało się potwierdzić archiwum backupu: {exc}") from exc

    return BackupRunResult(
        backup_name=backup_name,
        backup_path=backup_path,
        checksum=checksum,
        checksum_path=checksum_path,
        size_bytes=size_bytes,
        notes=notes,
        included_components=included_components,
        omitted_components=omitted_components,
        postgres_dump_included=postgres_dump_included,
    )


__all__ = [
    "BACKUP_DIR",
    "BackupFileInfo",
    "BackupRunError",
    "BackupRunResult",
    "create_local_backup",
    "format_backup_size",
    "list_backup_files",
    "prune_local_backups",
]
