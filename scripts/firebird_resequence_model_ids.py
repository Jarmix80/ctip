#!/usr/bin/env python3
"""Renumeruje MODEL.ID_MODEL na lokalnej/produkcyjnej bazie Firebird.

Scenariusz:
1. Zachowuje wszystkie rekordy MODEL z ID_MODEL <= max_stable_id (domyslnie 631).
2. Rekordy z ID_MODEL > max_stable_id przenumerowuje do kolejnych numerow od 632.
3. Aktualizuje powiazania ID_MODEL we wszystkich tabelach posiadajacych kolumne ID_MODEL.
4. Zapisuje raport JSON/MD z planem i wynikiem.

Skrypt domyslnie dziala w trybie dry-run. Zapis wykonuje tylko z flaga --apply.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = os.getenv("FB_LOCAL_COPY_PATH", "inbox/firebird/menadzer_serwisu.fdb")
DEFAULT_MODE = os.getenv("FB_MODE", "local").strip().lower()
DEFAULT_HOST = "127.0.0.1" if DEFAULT_MODE == "local" else os.getenv("FB_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.getenv("FB_PORT", "3050"))
DEFAULT_USER = os.getenv("FB_USER", "SYSDBA")
DEFAULT_PASSWORD = os.getenv("FB_PASSWORD", "")
DEFAULT_CHARSET = os.getenv("FB_CHARSET", "WIN1250")
DEFAULT_REPORT_DIR = REPO_ROOT / "inbox" / "audyt_model"
DEFAULT_MAX_STABLE_ID = 631
IDENTIFIER_PATTERN = re.compile(r"^[A-Z0-9_]+$")


@dataclass(frozen=True, slots=True)
class ModelRow:
    id_model: int
    marka: str
    model: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Renumeruje MODEL.ID_MODEL i aktualizuje wszystkie powiazania ID_MODEL "
            "w bazie Firebird."
        )
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="Host Firebird.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port Firebird.")
    parser.add_argument("--database", default=DEFAULT_DATABASE, help="Sciezka/alias bazy Firebird.")
    parser.add_argument("--user", default=DEFAULT_USER, help="Uzytkownik Firebird.")
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="Haslo Firebird.")
    parser.add_argument("--charset", default=DEFAULT_CHARSET, help="Charset polaczenia.")
    parser.add_argument(
        "--max-stable-id",
        type=int,
        default=DEFAULT_MAX_STABLE_ID,
        help="Najwyzszy ID_MODEL traktowany jako stabilny (domyslnie 631).",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=DEFAULT_REPORT_DIR,
        help="Katalog raportow.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Wykonaj zmiany w bazie. Bez tej flagi tylko plan i raport.",
    )
    parser.add_argument(
        "--skip-backup",
        action="store_true",
        help="Pomin backup plikowy lokalnej bazy przed --apply.",
    )
    return parser.parse_args()


def _looks_like_windows_database(path_value: str) -> bool:
    return bool(re.match(r"^[A-Za-z]:[/\\\\]", (path_value or "").strip()))


def _resolve_repo_path(path_value: str | Path) -> Path:
    path = Path(path_value).expanduser()
    if path.is_absolute():
        return path
    return (REPO_ROOT / path).resolve()


def resolve_database_value(path_value: str) -> tuple[str, Path | None]:
    raw_value = (path_value or "").strip()
    if _looks_like_windows_database(raw_value):
        return raw_value.replace("\\", "/"), None
    resolved = _resolve_repo_path(raw_value)
    return str(resolved), resolved


def connect_firebird(
    *, host: str, port: int, database: str, user: str, password: str, charset: str
):
    import firebirdsql  # type: ignore[import-not-found]

    return firebirdsql.connect(
        host=host,
        port=port,
        database=database,
        user=user,
        password=password,
        charset=charset,
    )


def ensure_identifier(identifier: str) -> str:
    normalized = str(identifier or "").strip().upper()
    if not IDENTIFIER_PATTERN.fullmatch(normalized):
        raise ValueError(f"Niepoprawny identyfikator SQL: {identifier}")
    return normalized


def load_tables_with_id_model(connection) -> list[str]:
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT TRIM(rf.RDB$RELATION_NAME)
            FROM RDB$RELATION_FIELDS rf
            JOIN RDB$RELATIONS r ON r.RDB$RELATION_NAME = rf.RDB$RELATION_NAME
            WHERE COALESCE(r.RDB$SYSTEM_FLAG, 0) = 0
              AND TRIM(rf.RDB$FIELD_NAME) = 'ID_MODEL'
            ORDER BY TRIM(rf.RDB$RELATION_NAME)
            """
        )
        return [ensure_identifier(row[0]) for row in cursor.fetchall()]
    finally:
        cursor.close()


def load_model_rows(connection) -> list[ModelRow]:
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT ID_MODEL, MARKA, MODEL
            FROM MODEL
            WHERE ID_MODEL IS NOT NULL
            ORDER BY ID_MODEL
            """
        )
        rows: list[ModelRow] = []
        for id_model, marka, model in cursor.fetchall():
            if id_model is None:
                continue
            rows.append(
                ModelRow(
                    id_model=int(id_model),
                    marka=str(marka or "").strip(),
                    model=str(model or "").strip(),
                )
            )
        return rows
    finally:
        cursor.close()


def build_model_id_mapping(existing_ids: list[int], *, max_stable_id: int) -> dict[int, int]:
    normalized_ids = sorted({int(value) for value in existing_ids if int(value) > max_stable_id})
    mapping: dict[int, int] = {}
    next_id = int(max_stable_id) + 1
    for old_id in normalized_ids:
        if old_id == next_id:
            next_id += 1
            continue
        mapping[old_id] = next_id
        next_id += 1
    return mapping


def collect_reference_counts_for_mapping(
    connection, *, tables: list[str], mapping_keys: list[int]
) -> dict[str, dict[int, int]]:
    if not mapping_keys:
        return {table: {} for table in tables}
    placeholders = ", ".join("?" for _ in mapping_keys)
    params = tuple(int(value) for value in mapping_keys)
    output: dict[str, dict[int, int]] = {}
    cursor = connection.cursor()
    try:
        for table in tables:
            cursor.execute(
                f"""
                SELECT ID_MODEL, COUNT(*)
                FROM {ensure_identifier(table)}
                WHERE ID_MODEL IN ({placeholders})
                GROUP BY ID_MODEL
                """,
                params,
            )
            output[table] = {
                int(id_model): int(total)
                for id_model, total in cursor.fetchall()
                if id_model is not None
            }
    finally:
        cursor.close()
    return output


def collect_orphan_id_model_references(connection, *, tables: list[str]) -> dict[str, list[int]]:
    output: dict[str, list[int]] = {}
    cursor = connection.cursor()
    try:
        for table in tables:
            if table == "MODEL":
                continue
            cursor.execute(
                f"""
                SELECT DISTINCT t.ID_MODEL
                FROM {ensure_identifier(table)} t
                WHERE t.ID_MODEL IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1
                      FROM MODEL m
                      WHERE m.ID_MODEL = t.ID_MODEL
                  )
                ORDER BY t.ID_MODEL
                """
            )
            output[table] = [int(row[0]) for row in cursor.fetchall() if row[0] is not None]
    finally:
        cursor.close()
    return output


def temp_model_id(value: int) -> int:
    return -abs(int(value))


def backup_local_database(database_path: Path, report_dir: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    backup_path = report_dir / f"{database_path.name}.model_resequence_{timestamp}.bak"
    shutil.copy2(database_path, backup_path)
    return backup_path


def apply_mapping(connection, *, mapping: dict[int, int], tables: list[str]) -> None:
    if not mapping:
        return
    cursor = connection.cursor()
    try:
        # Etap 1: MODEL -> tymczasowe ID ujemne, aby uniknac konfliktow unikalnosci.
        for old_id in sorted(mapping):
            cursor.execute(
                "UPDATE MODEL SET ID_MODEL = ? WHERE ID_MODEL = ?",
                (temp_model_id(old_id), old_id),
            )

        # Etap 2: przepiecie referencji we wszystkich tabelach poza MODEL.
        for table in tables:
            if table == "MODEL":
                continue
            for old_id, new_id in sorted(mapping.items()):
                cursor.execute(
                    f"UPDATE {ensure_identifier(table)} SET ID_MODEL = ? WHERE ID_MODEL = ?",
                    (new_id, old_id),
                )

        # Etap 3: MODEL -> docelowe nowe ID.
        for old_id, new_id in sorted(mapping.items()):
            cursor.execute(
                "UPDATE MODEL SET ID_MODEL = ? WHERE ID_MODEL = ?",
                (new_id, temp_model_id(old_id)),
            )
    finally:
        cursor.close()


def build_report_payload(
    *,
    database: str,
    host: str,
    port: int,
    max_stable_id: int,
    mode: str,
    model_rows: list[ModelRow],
    mapping: dict[int, int],
    reference_counts: dict[str, dict[int, int]],
    orphan_refs_before: dict[str, list[int]],
    orphan_refs_after: dict[str, list[int]] | None,
    backup_path: Path | None,
) -> dict[str, Any]:
    model_by_id = {row.id_model: row for row in model_rows}
    mapping_rows: list[dict[str, Any]] = []
    for old_id, new_id in sorted(mapping.items()):
        row = model_by_id.get(old_id)
        refs = {
            table: int(reference_counts.get(table, {}).get(old_id, 0))
            for table in sorted(reference_counts)
        }
        mapping_rows.append(
            {
                "old_id_model": old_id,
                "new_id_model": new_id,
                "marka": row.marka if row else "",
                "model": row.model if row else "",
                "references": refs,
                "references_total": sum(refs.values()),
            }
        )

    return {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "mode": mode,
        "database": database,
        "host": host,
        "port": port,
        "max_stable_id": max_stable_id,
        "model_total": len(model_rows),
        "model_stable_count": len([row for row in model_rows if row.id_model <= max_stable_id]),
        "model_resequence_count": len(mapping_rows),
        "mapping": mapping_rows,
        "orphan_references_before": orphan_refs_before,
        "orphan_references_after": orphan_refs_after,
        "backup_path": str(backup_path) if backup_path else None,
    }


def write_report(report_dir: Path, payload: dict[str, Any]) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    json_path = report_dir / f"model_id_resequence_{stamp}.json"
    md_path = report_dir / f"model_id_resequence_{stamp}.md"

    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines: list[str] = [
        "# Renumeracja MODEL.ID_MODEL",
        "",
        f"- Tryb: `{payload['mode']}`",
        f"- Baza: `{payload['database']}`",
        f"- Host/port: `{payload['host']}:{payload['port']}`",
        f"- `max_stable_id`: `{payload['max_stable_id']}`",
        f"- Rekordy MODEL: `{payload['model_total']}`",
        f"- Rekordy do renumeracji: `{payload['model_resequence_count']}`",
    ]
    if payload.get("backup_path"):
        lines.append(f"- Backup: `{payload['backup_path']}`")
    lines.extend(["", "## Mapowanie ID_MODEL", ""])
    if not payload["mapping"]:
        lines.append("Brak rekordow do renumeracji.")
    else:
        lines.extend(
            [
                "| old_id | new_id | marka | model | refs_total |",
                "|---:|---:|---|---|---:|",
            ]
        )
        for row in payload["mapping"]:
            lines.append(
                f"| {row['old_id_model']} | {row['new_id_model']} | "
                f"{row['marka']} | {row['model']} | {row['references_total']} |"
            )

    lines.extend(["", "## Osierocone referencje (przed)", ""])
    for table, values in sorted(payload["orphan_references_before"].items()):
        lines.append(f"- `{table}`: {len(values)}")
        if values:
            lines.append(f"  - ID: {', '.join(str(v) for v in values[:30])}")
    if payload.get("orphan_references_after") is not None:
        lines.extend(["", "## Osierocone referencje (po)", ""])
        for table, values in sorted(payload["orphan_references_after"].items()):
            lines.append(f"- `{table}`: {len(values)}")
            if values:
                lines.append(f"  - ID: {', '.join(str(v) for v in values[:30])}")

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def main() -> int:
    args = parse_args()
    database_value, local_database_path = resolve_database_value(args.database)
    report_dir = Path(args.report_dir)

    connection = connect_firebird(
        host=args.host,
        port=args.port,
        database=database_value,
        user=args.user,
        password=args.password,
        charset=args.charset,
    )
    backup_path: Path | None = None
    try:
        model_rows = load_model_rows(connection)
        model_ids = [row.id_model for row in model_rows]
        mapping = build_model_id_mapping(model_ids, max_stable_id=args.max_stable_id)
        tables = load_tables_with_id_model(connection)
        reference_counts = collect_reference_counts_for_mapping(
            connection,
            tables=tables,
            mapping_keys=sorted(mapping),
        )
        orphan_refs_before = collect_orphan_id_model_references(connection, tables=tables)

        if args.apply and mapping:
            if local_database_path and local_database_path.exists() and not args.skip_backup:
                report_dir.mkdir(parents=True, exist_ok=True)
                backup_path = backup_local_database(local_database_path, report_dir)
            apply_mapping(connection, mapping=mapping, tables=tables)
            orphan_refs_after = collect_orphan_id_model_references(connection, tables=tables)
            connection.commit()
            mode = "apply"
        else:
            orphan_refs_after = None
            mode = "dry-run"
            connection.rollback()

        payload = build_report_payload(
            database=database_value,
            host=args.host,
            port=args.port,
            max_stable_id=args.max_stable_id,
            mode=mode,
            model_rows=model_rows,
            mapping=mapping,
            reference_counts=reference_counts,
            orphan_refs_before=orphan_refs_before,
            orphan_refs_after=orphan_refs_after,
            backup_path=backup_path,
        )
        json_report, md_report = write_report(report_dir, payload)

        print(
            "Renumeracja MODEL zakonczona. "
            f"Tryb={mode}, rekordy_do_zmiany={len(mapping)}, "
            f"raport_json={json_report}, raport_md={md_report}"
        )
        return 0
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
