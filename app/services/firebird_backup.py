"""Tworzenie i weryfikacja logicznych kopii bazy Firebird Menadżera Serwisu."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import settings
from app.services.backup_artifacts import remove_files, write_checksum, write_json_atomic
from app.services.backup_runner import BackupRunError


@dataclass(slots=True)
class FirebirdBackupResult:
    """Wynik utworzenia i próbnego odtworzenia kopii Firebird."""

    backup_path: Path
    checksum_path: Path
    manifest_path: Path
    checksum: str
    size_bytes: int
    source_path: str
    verified: bool

    @property
    def files(self) -> list[Path]:
        """Zwraca kompletny zestaw plików przeznaczony do retencji i uploadu."""
        return [self.backup_path, self.checksum_path, self.manifest_path]


def _resolve_gbak(configured_path: str | None = None) -> str:
    """Wyszukuje narzędzie `gbak` w konfiguracji, PATH i katalogu Firebird."""
    configured = (configured_path or settings.firebird_gbak_path or "").strip().strip('"')
    if configured:
        path = Path(configured)
        if path.is_file():
            return str(path)
        raise BackupRunError(f"Skonfigurowane narzędzie gbak nie istnieje: {configured}")

    for executable in ("gbak.exe", "gbak"):
        resolved = shutil.which(executable)
        if resolved:
            return resolved

    if os.name == "nt":
        candidates: list[Path] = []
        for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
            program_files = os.environ.get(env_name)
            if program_files:
                candidates.extend(Path(program_files).glob("Firebird/Firebird_*/bin/gbak.exe"))
        if candidates:
            return str(max(candidates))
    raise BackupRunError("Nie znaleziono narzędzia gbak. Ustaw FIREBIRD_GBAK_PATH w pliku .env.")


def _process_error(result: subprocess.CompletedProcess[str]) -> str:
    """Zwraca skrócony komunikat procesu bez danych uwierzytelniających."""
    message = (result.stderr or result.stdout or "brak szczegółów").strip()
    return message[-1200:]


def _run_gbak(command: list[str], *, process_env: dict[str, str]) -> None:
    """Uruchamia `gbak` z limitem czasu i jednolitą obsługą błędów."""
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=process_env,
            timeout=settings.backup_firebird_timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BackupRunError(f"Nie udało się uruchomić gbak: {exc}") from exc
    if result.returncode != 0:
        raise BackupRunError(f"gbak zakończył się błędem: {_process_error(result)}")


def create_firebird_backup(
    *,
    source_path: str | Path,
    output_directory: Path,
    scope: str = "prod",
    now: datetime | None = None,
) -> FirebirdBackupResult:
    """Tworzy kopię `.fbk`, odtwarza ją próbnie i zapisuje manifest oraz SHA-256."""
    if scope not in {"prod", "test"}:
        raise BackupRunError("Zakres kopii Firebird musi mieć wartość prod albo test.")
    source = Path(source_path)
    if not source.is_file():
        raise BackupRunError(f"Plik źródłowy Firebird nie istnieje: {source}")

    generated_at = (now or datetime.now(UTC)).astimezone(UTC)
    stamp = generated_at.strftime("%Y%m%d_%H%M%S")
    base_name = f"ctip_firebird_{scope}_{stamp}"
    output_directory.mkdir(parents=True, exist_ok=True)
    staging_directory = output_directory / ".staging"
    verify_directory = output_directory / ".verify"
    staging_directory.mkdir(parents=True, exist_ok=True)
    verify_directory.mkdir(parents=True, exist_ok=True)

    backup_path = output_directory / f"{base_name}.fbk"
    partial_path = staging_directory / f"{base_name}.fbk.partial"
    verify_path = verify_directory / f"{base_name}_restore.fdb"
    checksum_path = backup_path.with_name(f"{backup_path.name}.sha256")
    manifest_path = output_directory / f"{base_name}_manifest.json"
    generated_files = [partial_path, verify_path, backup_path, checksum_path, manifest_path]
    remove_files(generated_files)

    process_env = os.environ.copy()
    process_env["ISC_USER"] = settings.fb_user
    process_env["ISC_PASSWORD"] = settings.fb_password
    gbak = _resolve_gbak()
    try:
        _run_gbak(
            [gbak, "-b", "-g", "-v", str(source), str(partial_path)],
            process_env=process_env,
        )
        if not partial_path.is_file() or partial_path.stat().st_size == 0:
            raise BackupRunError("gbak nie utworzył poprawnego pliku kopii Firebird.")

        _run_gbak(
            [gbak, "-c", "-v", str(partial_path), str(verify_path)],
            process_env=process_env,
        )
        if not verify_path.is_file() or verify_path.stat().st_size == 0:
            raise BackupRunError("Próbne odtworzenie kopii Firebird nie utworzyło bazy.")
        verify_path.unlink(missing_ok=True)
        partial_path.replace(backup_path)

        checksum, checksum_path = write_checksum(backup_path)
        size_bytes = backup_path.stat().st_size
        write_json_atomic(
            manifest_path,
            {
                "type": "firebird",
                "scope": scope,
                "generated_at_utc": generated_at.isoformat(),
                "source_path": str(source),
                "backup_file": backup_path.name,
                "checksum_file": checksum_path.name,
                "sha256": checksum,
                "size_bytes": size_bytes,
                "verification": "gbak_restore_success",
            },
        )
    except (BackupRunError, OSError) as exc:
        remove_files(generated_files)
        if isinstance(exc, BackupRunError):
            raise
        raise BackupRunError(f"Nie udało się zapisać kopii Firebird: {exc}") from exc

    return FirebirdBackupResult(
        backup_path=backup_path,
        checksum_path=checksum_path,
        manifest_path=manifest_path,
        checksum=checksum,
        size_bytes=size_bytes,
        source_path=str(source),
        verified=True,
    )


__all__ = ["FirebirdBackupResult", "create_firebird_backup"]
