"""Porównuje bieżące rewizje bazy z głowami grafu Alembic bez migracji."""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Sequence

REVISION_RE = re.compile(r"^([0-9a-f]{12,40})\b", re.IGNORECASE)


def revisions_from_output(output: str) -> set[str]:
    """Wyodrębnia identyfikatory rewizji z wyjścia polecenia Alembic."""
    revisions: set[str] = set()
    for raw_line in output.splitlines():
        match = REVISION_RE.match(raw_line.strip())
        if match:
            revisions.add(match.group(1).lower())
    return revisions


def run_alembic(arguments: Sequence[str]) -> str:
    """Uruchamia Alembic, oceniając kod wyjścia zamiast samego stderr."""
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        if completed.stderr:
            print(completed.stderr.rstrip(), file=sys.stderr)
        raise RuntimeError(f"Alembic zakończył się kodem {completed.returncode}.")
    return completed.stdout


def main() -> int:
    """Zwraca sukces wyłącznie, gdy baza jest na wszystkich głowach grafu."""
    try:
        heads = revisions_from_output(run_alembic(["heads"]))
        current = revisions_from_output(run_alembic(["current"]))
    except RuntimeError as exc:
        print(f"[BŁĄD] {exc}", file=sys.stderr)
        return 1
    if not heads:
        print("[BŁĄD] Nie udało się ustalić głów grafu Alembic.", file=sys.stderr)
        return 1
    if current != heads:
        print(
            "[BŁĄD] Testowa baza nie jest na głowie grafu Alembic: "
            f"baza={sorted(current)}, głowy={sorted(heads)}.",
            file=sys.stderr,
        )
        return 1
    print(f"[OK] Testowa baza jest na rewizjach: {', '.join(sorted(current))}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
