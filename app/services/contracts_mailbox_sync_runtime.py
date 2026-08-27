"""Wspólne helpery uruchamiania synchronizacji mailboxa umów."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


def parse_mailbox_sync_summary(value: str) -> dict[str, int] | None:
    """Parsuje podsumowanie liczbowe ze standardowego wyjścia synchronizacji."""
    summary_line = ""
    for line in reversed(value.splitlines()):
        if "Analizowane:" in line and "zaktualizowane:" in line:
            summary_line = line
            break
    if not summary_line:
        return None

    counts = [int(item) for item in re.findall(r"\d+", summary_line)]
    if len(counts) < 8:
        return None
    summary = {
        "analysed": counts[0],
        "updated": counts[1],
        "skipped_state": counts[2],
        "warnings": counts[3],
        "unknown_subjects": counts[4],
        "unmatched_forms": counts[5],
        "ambiguous_matches": counts[6],
        "unresolved_open": counts[7],
    }
    if len(counts) >= 9:
        summary["manual_hold"] = counts[8]
    return summary


def run_mailbox_sync_subprocess(
    *,
    limit: int,
    folder: str,
    reprocess: bool,
    dry_run: bool,
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    """Uruchamia skrypt synchronizacji mailboxa jako podproces."""
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "contracts_mailbox_sync.py"
    command = [sys.executable, str(script_path), "--limit", str(limit), "--folder", folder]
    if reprocess:
        command.append("--reprocess")
    if dry_run:
        command.append("--dry-run")
    return subprocess.run(
        command,
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout_seconds,
    )
