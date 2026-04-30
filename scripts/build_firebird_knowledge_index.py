"""Buduje lokalny indeks wiedzy Firebird MS na podstawie repozytorium bazams.

Skrypt zbiera:
- opisy tabel i kolumn z `docs/structure/*.md`,
- dokumenty opisowe z `docs/*.md`,
- analizy reverse z `reverse/analysis/*.md`.

Wynik zapisuje do:
`docs/firebird/knowledge/firebird_ms_knowledge.json`
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def resolve_bazams_root(explicit: str | None = None) -> Path:
    if explicit:
        candidate = Path(explicit).expanduser().resolve()
        if candidate.exists():
            return candidate
        raise RuntimeError(f"Podana ścieżka bazams nie istnieje: {candidate}")

    default_candidates = [
        repo_root() / "integrations" / "bazams",
        repo_root() / "docs" / "firebird" / "external" / "bazams",
    ]
    for candidate in default_candidates:
        if candidate.exists():
            return candidate
    raise RuntimeError(
        "Nie znaleziono repozytorium bazams. "
        "Sklonuj je do `integrations/bazams` albo podaj `--source`."
    )


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def parse_structure_table(path: Path) -> dict[str, Any]:
    content = read_text(path)
    lines = content.splitlines()

    table_name = path.stem.upper()
    primary_key = None
    description = None
    column_count = None
    intro_lines: list[str] = []

    in_intro = True
    for line in lines:
        if line.startswith("## Kolumny"):
            in_intro = False
        if in_intro and line.strip().startswith("- "):
            intro_lines.append(line.strip()[2:].strip())
        if line.startswith("- Klucz główny:"):
            primary_key = line.split(":", 1)[1].strip()
        if line.startswith("- Wstępny opis:"):
            description = line.split(":", 1)[1].strip()
        if line.startswith("- Liczba kolumn:"):
            raw = line.split(":", 1)[1].strip()
            try:
                column_count = int(raw)
            except ValueError:
                column_count = None

    columns: list[dict[str, Any]] = []
    in_columns = False
    for idx, line in enumerate(lines):
        if line.strip().startswith("| Kolumna | Typ | Nullable | Domyślna wartość | Opis |"):
            if idx + 1 < len(lines) and lines[idx + 1].strip().startswith("| --- |"):
                in_columns = True
            continue
        if not in_columns:
            continue
        if not line.strip().startswith("|"):
            break
        parts = [part.strip() for part in line.strip().split("|")]
        if len(parts) < 7:
            continue
        column_name, col_type, nullable, default_value, col_description = (
            parts[1],
            parts[2],
            parts[3],
            parts[4],
            parts[5],
        )
        if not column_name or column_name.lower() == "kolumna" or set(column_name) == {"-"}:
            continue
        columns.append(
            {
                "column_name": column_name,
                "column_type": col_type,
                "nullable": nullable,
                "default_value": default_value,
                "description": col_description,
            }
        )

    if column_count is None:
        column_count = len(columns)

    return {
        "table_name": table_name,
        "primary_key": primary_key,
        "description": description,
        "column_count": column_count,
        "intro": intro_lines,
        "columns": columns,
        "source_path": str(path.relative_to(repo_root())),
    }


def compact_markdown(content: str, *, max_len: int = 25000) -> str:
    compact = re.sub(r"\n{3,}", "\n\n", content).strip()
    if len(compact) > max_len:
        return compact[:max_len]
    return compact


def resolve_git_commit(path: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:  # noqa: BLE001
        return None
    value = (result.stdout or "").strip()
    return value or None


def build_index(bazams_root: Path) -> dict[str, Any]:
    structure_dir = bazams_root / "docs" / "structure"
    if not structure_dir.exists():
        raise RuntimeError(f"Brak katalogu struktur: {structure_dir}")

    table_files = sorted(structure_dir.glob("*.md"))
    if not table_files:
        raise RuntimeError(f"Brak plików tabel w: {structure_dir}")

    table_items = [parse_structure_table(path) for path in table_files]

    document_items: list[dict[str, Any]] = []
    for path in sorted((bazams_root / "docs").glob("*.md")):
        document_items.append(
            {
                "kind": "doc",
                "title": path.stem,
                "source_path": str(path.relative_to(repo_root())),
                "content": compact_markdown(read_text(path)),
            }
        )
    for path in sorted((bazams_root / "reverse" / "analysis").glob("*.md")):
        document_items.append(
            {
                "kind": "analysis",
                "title": path.stem,
                "source_path": str(path.relative_to(repo_root())),
                "content": compact_markdown(read_text(path)),
            }
        )

    return {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "knowledge_source": {
            "name": "bazams",
            "path": str(bazams_root.relative_to(repo_root())),
            "commit": resolve_git_commit(bazams_root),
        },
        "table_count": len(table_items),
        "tables": table_items,
        "document_count": len(document_items),
        "documents": document_items,
    }


def write_index(payload: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Buduje indeks wiedzy Firebird MS dla CTIP.")
    parser.add_argument(
        "--source",
        help="Opcjonalna ścieżka do repozytorium bazams (domyślnie integrations/bazams).",
    )
    parser.add_argument(
        "--output",
        help="Opcjonalna ścieżka wyjściowa JSON.",
    )
    args = parser.parse_args()

    source_root = resolve_bazams_root(args.source)
    output_path = (
        Path(args.output).expanduser().resolve()
        if args.output
        else repo_root() / "docs" / "firebird" / "knowledge" / "firebird_ms_knowledge.json"
    )
    payload = build_index(source_root)
    write_index(payload, output_path)
    print(f"Zapisano indeks wiedzy Firebird: {output_path}")
    print(f"Tabele: {payload['table_count']}, dokumenty: {payload['document_count']}")


if __name__ == "__main__":
    main()
