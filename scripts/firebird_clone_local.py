"""Tworzenie lokalnej kopii roboczej bazy Firebird z pliku źródłowego."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Kopiuje bazę Firebird do lokalnej ścieżki roboczej. "
            "Domyślnie używa FB_DATABASE i FB_LOCAL_COPY_PATH."
        )
    )
    parser.add_argument("--source", default=os.getenv("FB_DATABASE", ""), help="Plik źródłowy .fdb")
    parser.add_argument(
        "--target",
        default=os.getenv("FB_LOCAL_COPY_PATH", ""),
        help="Docelowy plik lokalnej kopii .fdb",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Nadpisz istniejącą kopię docelową.",
    )
    return parser.parse_args()


def _resolve_windows_source(path_value: str) -> Path:
    """Zamienia ścieżkę typu `D:/katalog/plik.fdb` na `/mnt/d/katalog/plik.fdb`."""
    if not path_value:
        return Path(path_value)
    normalized = path_value.replace("\\", "/")
    match = re.match(r"^([A-Za-z]):/(.+)$", normalized)
    if not match:
        return Path(normalized)
    drive = match.group(1).lower()
    rest = match.group(2)
    return Path("/mnt") / drive / rest


def main() -> int:
    args = parse_args()
    source_raw = (args.source or "").strip()
    source = _resolve_windows_source(source_raw).expanduser()
    target = Path(args.target).expanduser()

    if not source_raw:
        print("Błąd: brak źródła. Ustaw FB_DATABASE albo podaj --source.", file=sys.stderr)
        return 2
    if not target.as_posix():
        print("Błąd: brak celu. Ustaw FB_LOCAL_COPY_PATH albo podaj --target.", file=sys.stderr)
        return 2
    if not source.exists():
        print(
            f"Błąd: plik źródłowy nie istnieje: {source} (wejście: {source_raw})",
            file=sys.stderr,
        )
        return 2
    if not source.is_file():
        print(f"Błąd: źródło nie jest plikiem: {source}", file=sys.stderr)
        return 2
    if target.exists() and not args.force:
        print(
            f"Błąd: plik docelowy już istnieje: {target}. Użyj --force, aby nadpisać.",
            file=sys.stderr,
        )
        return 2

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_target = target.with_suffix(target.suffix + ".tmp")
    shutil.copy2(source, tmp_target)
    tmp_target.replace(target)
    print(f"OK: utworzono lokalną kopię Firebird: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
