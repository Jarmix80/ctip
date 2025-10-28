"""
Narzędzia pomocnicze do tworzenia dziennych logów tekstowych w katalogu `docs/LOG`.

Każdy wpis otrzymuje znacznik czasu (YYYY-MM-DD HH:MM:SS), a pliki są rotowane
per dzień poprzez dodanie daty w nazwie, np. `log_collector_2025-10-10.log`.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

LOG_ROOT = Path("docs/LOG")


def _ensure_directory(subdir: str) -> Path:
    path = LOG_ROOT / subdir
    path.mkdir(parents=True, exist_ok=True)
    return path


def daily_log_path(subdir: str, base_name: str, now: datetime | None = None) -> Path:
    """Zwraca ścieżkę do pliku logu dla bieżącej daty (YYYY-MM-DD)."""
    now = now or datetime.now()
    directory = _ensure_directory(subdir)
    date_str = now.strftime("%Y-%m-%d")
    filename = f"{base_name}_{date_str}.log"
    return directory / filename


def append_log(subdir: str, base_name: str, message: str, now: datetime | None = None) -> Path:
    """
    Dopisuje pojedynczy wiersz logu z datą i godziną.

    Zwraca ścieżkę do pliku, do którego został dopisany wpis.
    """
    now = now or datetime.now()
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    sanitized = message.rstrip("\n")
    path = daily_log_path(subdir, base_name, now)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}] {sanitized}\n")
    return path


def read_log_tail(
    subdir: str,
    base_name: str,
    limit: int = 100,
    now: datetime | None = None,
) -> list[str]:
    """
    Zwraca ostatnie `limit` wierszy dziennego pliku logu.

    Przy braku pliku zwraca pustą listę.
    """
    if limit <= 0:
        return []
    now = now or datetime.now()
    path = daily_log_path(subdir, base_name, now)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        lines = handle.readlines()
    if len(lines) <= limit:
        return [line.rstrip("\n") for line in lines]
    return [line.rstrip("\n") for line in lines[-limit:]]
