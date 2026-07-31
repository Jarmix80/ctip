"""Tworzenie i weryfikacja natywnych kopii baz SQL Server systemu Optima."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import settings
from app.services.backup_artifacts import remove_files, write_checksum, write_json_atomic
from app.services.backup_runner import BackupRunError

_DATABASE_NAME_PATTERN = re.compile(r"^[0-9A-Za-z_]+$")


@dataclass(slots=True)
class OptimaDatabaseBackup:
    """Metadane natywnej kopii pojedynczej bazy Optimy."""

    database_name: str
    backup_path: Path
    checksum_path: Path
    checksum: str
    size_bytes: int


@dataclass(slots=True)
class OptimaBackupResult:
    """Wynik kompletnego przebiegu kopii baz Optimy."""

    database_backups: list[OptimaDatabaseBackup]
    manifest_path: Path
    restore_verified_database: str
    verified: bool

    @property
    def database_names(self) -> list[str]:
        """Zwraca nazwy baz objętych kompletnym przebiegiem."""
        return [item.database_name for item in self.database_backups]

    @property
    def files(self) -> list[Path]:
        """Zwraca wszystkie pliki przebiegu przeznaczone do uploadu i retencji."""
        files: list[Path] = []
        for item in self.database_backups:
            files.extend((item.backup_path, item.checksum_path))
        files.append(self.manifest_path)
        return files


def _resolve_sqlcmd(configured_path: str | None = None) -> str:
    """Wyszukuje `sqlcmd` w konfiguracji, PATH i katalogach Microsoft SQL Server."""
    configured = (configured_path or settings.optima_sqlcmd_path or "").strip().strip('"')
    if configured:
        path = Path(configured)
        if path.is_file():
            return str(path)
        raise BackupRunError(f"Skonfigurowane narzędzie sqlcmd nie istnieje: {configured}")

    for executable in ("sqlcmd.exe", "sqlcmd"):
        resolved = shutil.which(executable)
        if resolved:
            return resolved

    if os.name == "nt":
        candidates: list[Path] = []
        for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
            program_files = os.environ.get(env_name)
            if program_files:
                root = Path(program_files) / "Microsoft SQL Server"
                candidates.extend(root.glob("Client SDK/ODBC/*/Tools/Binn/SQLCMD.EXE"))
                candidates.extend(root.glob("*/Tools/Binn/SQLCMD.EXE"))
        if candidates:
            return str(max(candidates))
    raise BackupRunError("Nie znaleziono narzędzia sqlcmd. Ustaw OPTIMA_SQLCMD_PATH w pliku .env.")


def _sql_literal(value: str) -> str:
    return value.replace("'", "''")


def _server_target() -> str:
    instance = (settings.optima_sql_server_instance or "").strip()
    if instance:
        return instance
    host = (settings.optima_sql_host or settings.optima_sql_host_ip or "").strip()
    if not host:
        raise BackupRunError("Brak OPTIMA_SQL_SERVER_INSTANCE albo OPTIMA_SQL_HOST w pliku .env.")
    return f"{host},{settings.optima_sql_port}"


def _validated_database_names(database_names: list[str]) -> list[str]:
    """Sprawdza kompletność, unikalność i bezpieczny format nazw baz."""
    normalized = [name.strip() for name in database_names if name and name.strip()]
    if not normalized:
        raise BackupRunError("Nie skonfigurowano żadnej bazy SQL Optimy do archiwizacji.")
    if len(normalized) != len(database_names):
        raise BackupRunError("W konfiguracji baz Optimy znajduje się pusta nazwa.")
    if len(set(normalized)) != len(normalized):
        raise BackupRunError("Nazwy baz Optimy muszą być unikalne.")
    invalid = [name for name in normalized if not _DATABASE_NAME_PATTERN.fullmatch(name)]
    if invalid:
        raise BackupRunError(f"Niedozwolona nazwa bazy Optimy: {invalid[0]}")
    return normalized


def _sqlcmd_context() -> tuple[list[str], dict[str, str]]:
    """Buduje bazowe argumenty i środowisko `sqlcmd` bez hasła w linii poleceń."""
    command = [
        _resolve_sqlcmd(),
        "-S",
        _server_target(),
        "-b",
        "-r",
        "1",
        "-l",
        "30",
        "-t",
        str(settings.backup_optima_timeout_seconds),
    ]
    process_env = os.environ.copy()
    auth_mode = (settings.optima_sql_auth_mode or "windows").strip().lower()
    if auth_mode == "windows":
        command.append("-E")
    elif auth_mode in {"mixed", "sql"}:
        login = (settings.optima_sql_login or "").strip()
        password = settings.optima_sql_password or ""
        if not login or not password:
            raise BackupRunError("Dla logowania SQL Optimy wymagane są login i hasło w .env.")
        command.extend(("-U", login))
        process_env["SQLCMDPASSWORD"] = password
    else:
        raise BackupRunError("OPTIMA_SQL_AUTH_MODE musi mieć wartość windows, mixed albo sql.")
    return command, process_env


def _run_sqlcmd(query: str, *, tabular: bool = False) -> subprocess.CompletedProcess[str]:
    """Wykonuje zapytanie przez `sqlcmd` i zwraca przechwycony rezultat."""
    base_command, process_env = _sqlcmd_context()
    if tabular:
        base_command.extend(("-h", "-1", "-W", "-s", "|"))
    command = [*base_command, "-Q", query]
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=process_env,
            timeout=settings.backup_optima_timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BackupRunError(f"Nie udało się uruchomić sqlcmd: {exc}") from exc
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "brak szczegółów").strip()[-1600:]
        raise BackupRunError(f"sqlcmd zakończył się błędem: {message}")
    return result


def _backup_database(database_name: str, backup_path: Path) -> None:
    """Tworzy `COPY_ONLY` i wykonuje `RESTORE VERIFYONLY WITH CHECKSUM`."""
    escaped_path = _sql_literal(str(backup_path.resolve()))
    escaped_name = _sql_literal(database_name)
    query = f"""
SET NOCOUNT ON;
IF DB_ID(N'{escaped_name}') IS NULL
    THROW 51000, N'Nie znaleziono skonfigurowanej bazy Optimy.', 1;
IF EXISTS (SELECT 1 FROM sys.databases WHERE name = N'{escaped_name}' AND state_desc <> N'ONLINE')
    THROW 51001, N'Baza Optimy nie jest w stanie ONLINE.', 1;
BACKUP DATABASE [{database_name}]
TO DISK = N'{escaped_path}'
WITH COPY_ONLY, INIT, CHECKSUM, STATS = 10;
RESTORE VERIFYONLY FROM DISK = N'{escaped_path}' WITH CHECKSUM;
"""
    _run_sqlcmd(query)
    if not backup_path.is_file() or backup_path.stat().st_size == 0:
        raise BackupRunError(f"SQL Server nie utworzył poprawnej kopii bazy {database_name}.")


def _read_backup_logical_files(backup_path: Path) -> list[tuple[str, str]]:
    """Odczytuje nazwy logiczne plików danych i logu z natywnej kopii SQL."""
    escaped_path = _sql_literal(str(backup_path.resolve()))
    result = _run_sqlcmd(
        f"SET NOCOUNT ON; RESTORE FILELISTONLY FROM DISK = N'{escaped_path}';",
        tabular=True,
    )
    logical_files: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        columns = [column.strip() for column in line.split("|")]
        if len(columns) >= 3 and columns[0] and columns[2] in {"D", "L"}:
            logical_files.append((columns[0], columns[2]))
    if not logical_files:
        raise BackupRunError("RESTORE FILELISTONLY nie zwrócił plików kontrolowanej kopii Optimy.")
    return logical_files


def _verify_database_restore(
    *,
    database_name: str,
    backup_path: Path,
    verify_directory: Path,
) -> None:
    """Odtwarza kopię pod nazwą tymczasową i uruchamia `DBCC CHECKDB PHYSICAL_ONLY`."""
    logical_files = _read_backup_logical_files(backup_path)
    temp_database = f"CTIP_VERIFY_{uuid.uuid4().hex[:12].upper()}"
    move_clauses: list[str] = []
    verify_files: list[Path] = []
    data_index = 0
    log_index = 0
    for logical_name, file_type in logical_files:
        if file_type == "L":
            log_index += 1
            extension = ".ldf" if log_index == 1 else f"_{log_index}.ldf"
        else:
            data_index += 1
            extension = ".mdf" if data_index == 1 else f"_{data_index}.ndf"
        destination = verify_directory / f"{temp_database}{extension}"
        verify_files.append(destination)
        move_clauses.append(
            f"MOVE N'{_sql_literal(logical_name)}' TO N'{_sql_literal(str(destination.resolve()))}'"
        )

    escaped_backup = _sql_literal(str(backup_path.resolve()))
    restore_query = f"""
SET NOCOUNT ON;
RESTORE DATABASE [{temp_database}]
FROM DISK = N'{escaped_backup}'
WITH {', '.join(move_clauses)}, RECOVERY;
DBCC CHECKDB ([{temp_database}]) WITH PHYSICAL_ONLY, NO_INFOMSGS;
"""
    verification_error: BackupRunError | None = None
    try:
        _run_sqlcmd(restore_query)
    except BackupRunError as exc:
        verification_error = exc

    cleanup_query = f"""
SET NOCOUNT ON;
IF DB_ID(N'{temp_database}') IS NOT NULL
BEGIN
    ALTER DATABASE [{temp_database}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
    DROP DATABASE [{temp_database}];
END;
"""
    try:
        _run_sqlcmd(cleanup_query)
    except BackupRunError as exc:
        if verification_error is None:
            verification_error = BackupRunError(
                f"Kontrolna baza {temp_database} nie została usunięta: {exc}"
            )
    remove_files(verify_files)
    if verification_error is not None:
        raise BackupRunError(
            f"Kontrolowane odtworzenie kopii bazy {database_name} nie powiodło się: "
            f"{verification_error}"
        ) from verification_error


def create_optima_backup(
    *,
    database_names: list[str],
    output_directory: Path,
    restore_test_database: str | None = None,
    now: datetime | None = None,
) -> OptimaBackupResult:
    """Tworzy kompletny zestaw `.bak` i kontrolnie odtwarza wskazaną bazę."""
    databases = _validated_database_names(database_names)
    restore_database = restore_test_database or databases[0]
    if restore_database not in databases:
        raise BackupRunError("Baza do kontrolnego odtworzenia nie należy do zestawu Optimy.")

    generated_at = (now or datetime.now(UTC)).astimezone(UTC)
    stamp = generated_at.strftime("%Y%m%d_%H%M%S")
    prefix = f"ctip_optima_{stamp}"
    output_directory.mkdir(parents=True, exist_ok=True)
    staging_directory = output_directory / ".staging"
    verify_directory = output_directory / ".verify"
    staging_directory.mkdir(parents=True, exist_ok=True)
    verify_directory.mkdir(parents=True, exist_ok=True)

    manifest_path = output_directory / f"{prefix}_manifest.json"
    final_paths = {
        database: output_directory / f"{prefix}_{database}.bak" for database in databases
    }
    partial_paths = {
        database: staging_directory / f"{prefix}_{database}.bak.partial" for database in databases
    }
    cleanup_paths = [manifest_path]
    cleanup_paths.extend(final_paths.values())
    cleanup_paths.extend(partial_paths.values())
    cleanup_paths.extend(path.with_name(f"{path.name}.sha256") for path in final_paths.values())
    remove_files(cleanup_paths)

    database_backups: list[OptimaDatabaseBackup] = []
    try:
        for database in databases:
            _backup_database(database, partial_paths[database])

        _verify_database_restore(
            database_name=restore_database,
            backup_path=partial_paths[restore_database],
            verify_directory=verify_directory,
        )

        for database in databases:
            partial_paths[database].replace(final_paths[database])
            checksum, checksum_path = write_checksum(final_paths[database])
            database_backups.append(
                OptimaDatabaseBackup(
                    database_name=database,
                    backup_path=final_paths[database],
                    checksum_path=checksum_path,
                    checksum=checksum,
                    size_bytes=final_paths[database].stat().st_size,
                )
            )

        write_json_atomic(
            manifest_path,
            {
                "type": "optima_sql_server",
                "generated_at_utc": generated_at.isoformat(),
                "server": _server_target(),
                "databases": [
                    {
                        "name": item.database_name,
                        "backup_file": item.backup_path.name,
                        "checksum_file": item.checksum_path.name,
                        "sha256": item.checksum,
                        "size_bytes": item.size_bytes,
                    }
                    for item in database_backups
                ],
                "backup_options": ["COPY_ONLY", "INIT", "CHECKSUM"],
                "verification": {
                    "verifyonly_with_checksum": True,
                    "restore_database": restore_database,
                    "dbcc_checkdb": "PHYSICAL_ONLY",
                    "temporary_database_removed": True,
                },
            },
        )
    except (BackupRunError, OSError) as exc:
        remove_files(cleanup_paths)
        if isinstance(exc, BackupRunError):
            raise
        raise BackupRunError(f"Nie udało się zapisać kopii baz Optimy: {exc}") from exc

    return OptimaBackupResult(
        database_backups=database_backups,
        manifest_path=manifest_path,
        restore_verified_database=restore_database,
        verified=True,
    )


__all__ = ["OptimaBackupResult", "OptimaDatabaseBackup", "create_optima_backup"]
