"""Operacje wspólne dla samodzielnych artefaktów kopii zapasowych."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    """Oblicza sumę SHA-256 pliku bez wczytywania go w całości do pamięci."""
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def write_checksum(path: Path) -> tuple[str, Path]:
    """Tworzy plik boczny SHA-256 i zwraca sumę wraz z jego ścieżką."""
    checksum = sha256_file(path)
    checksum_path = path.with_name(f"{path.name}.sha256")
    checksum_path.write_text(f"{checksum}  {path.name}\n", encoding="utf-8")
    return checksum, checksum_path


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Zapisuje dokument JSON przez plik tymczasowy w tym samym katalogu."""
    temporary_path = path.with_name(f".{path.name}.tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)


def remove_files(paths: list[Path]) -> None:
    """Usuwa wskazane pliki, ignorując brakujące elementy i błędy porządkowe."""
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            continue


__all__ = ["remove_files", "sha256_file", "write_checksum", "write_json_atomic"]
