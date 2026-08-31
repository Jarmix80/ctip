#!/usr/bin/env python3
"""Naprawia tabele MODEL na podstawie zatwierdzonego snapshotu referencyjnego.

Skrypt sluzy do testu i pozniejszego wdrozenia naprawy na bazie produkcyjnej.
Logika:
1. Snapshot referencyjny jest traktowany jako approved master tabeli MODEL.
2. Rekordy MODEL obecne tylko w bazie docelowej sa mapowane do approved ID_MODEL
   po sygnaturze MARKA+MODEL (najpierw exact, potem po rodzinie producenta).
3. Referencje w MASZYNA, MAGAZYN, CENNIK i MZ sa przepinane na pozostawiony
   model.
4. Approved rekordy sa insertowane / aktualizowane 1:1 ze snapshotu.
5. Nadmiarowe rekordy MODEL sa usuwane.
6. Na koncu wykonywana jest walidacja zgodnosci MODEL ze snapshotem.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import sys
from collections import defaultdict
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
REFERENCE_TABLES = ("MASZYNA", "MAGAZYN", "CENNIK", "MZ")
RICOH_FAMILY = {"RICOH", "NASHUATEC", "GESTETNER", "LANIER", "INFOTEC"}


@dataclass(frozen=True, slots=True)
class ExtraModelPlan:
    id_model: int
    marka: str
    model: str
    refs_maszyna: int
    refs_magazyn: int
    refs_cennik: int
    refs_mz: int
    target_id_model: int | None
    target_marka: str
    target_model: str
    match_reason: str
    action: str

    @property
    def refs_total(self) -> int:
        return self.refs_maszyna + self.refs_magazyn + self.refs_cennik + self.refs_mz


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Naprawia MODEL na bazie zatwierdzonego snapshotu i przepina referencje "
            "w MASZYNA/MAGAZYN/CENNIK/MZ."
        )
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="Host Firebird.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port Firebird.")
    parser.add_argument(
        "--reference-host",
        default="127.0.0.1",
        help="Host Firebird dla snapshotu referencyjnego.",
    )
    parser.add_argument(
        "--reference-port",
        type=int,
        default=DEFAULT_PORT,
        help="Port Firebird dla snapshotu referencyjnego.",
    )
    parser.add_argument("--database", default=DEFAULT_DATABASE, help="Docelowa baza Firebird.")
    parser.add_argument(
        "--reference-database",
        default=_discover_reference_database(),
        help="Snapshot referencyjny z approved tabela MODEL.",
    )
    parser.add_argument("--user", default=DEFAULT_USER, help="Uzytkownik Firebird.")
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="Haslo Firebird.")
    parser.add_argument("--charset", default=DEFAULT_CHARSET, help="Charset polaczenia.")
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=DEFAULT_REPORT_DIR,
        help="Katalog raportow.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Wykonaj zapis do bazy docelowej. Bez tej flagi skrypt robi tylko plan i walidacje.",
    )
    parser.add_argument(
        "--skip-backup",
        action="store_true",
        help="Pomin backup plikowy skryptu. Uzywaj tylko gdy backup zostal wykonany osobno.",
    )
    return parser.parse_args()


def _discover_reference_database() -> str:
    candidates = sorted(
        (REPO_ROOT / "inbox" / "firebird").glob("menadzer_serwisu_snapshot_po_rodzaj_mfp_*.fdb")
    )
    if not candidates:
        return ""
    return str(candidates[-1])


def _resolve_repo_path(path_value: str | Path) -> Path:
    path = Path(path_value).expanduser()
    if path.is_absolute():
        return path
    return (REPO_ROOT / path).resolve()


def _looks_like_windows_database(path_value: str) -> bool:
    return bool(re.match(r"^[A-Za-z]:[/\\\\]", (path_value or "").strip()))


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


def load_columns(connection, table: str) -> list[str]:
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT TRIM(rf.RDB$FIELD_NAME)
            FROM RDB$RELATION_FIELDS rf
            WHERE TRIM(rf.RDB$RELATION_NAME) = ?
            ORDER BY rf.RDB$FIELD_POSITION
            """,
            (table,),
        )
        return [row[0] for row in cursor.fetchall()]
    finally:
        cursor.close()


def load_rows_by_id(
    connection, table: str, columns: list[str], id_field: str
) -> dict[int, dict[str, Any]]:
    cursor = connection.cursor()
    try:
        cursor.execute(f"SELECT {', '.join(columns)} FROM {table} ORDER BY {id_field}")
        rows: dict[int, dict[str, Any]] = {}
        for row in cursor.fetchall():
            payload = dict(zip(columns, row, strict=True))
            row_id = payload[id_field]
            if row_id is None:
                continue
            rows[int(row_id)] = payload
        return rows
    finally:
        cursor.close()


def normalize_text(value: Any) -> str:
    text = str(value or "").strip().upper().replace("+", " PLUS ")
    text = re.sub(r"\s+", " ", text)
    return text


def normalize_brand_family(value: Any) -> str:
    brand = normalize_text(value)
    if brand in RICOH_FAMILY:
        return "RICOH"
    if any(token in brand for token in ("KONICA", "BIZHUB", "DEVELOP", "INEO")):
        return "KONICA MINOLTA"
    if any(token in brand for token in ("KYOCERA", "ECOSYS", "TASKALFA", "UTAX")):
        return "KYOCERA"
    return brand


def build_reference_maps(
    reference_rows: dict[int, dict[str, Any]],
) -> tuple[dict[tuple[str, str], int], dict[tuple[str, str], int]]:
    exact_candidates: dict[tuple[str, str], set[int]] = defaultdict(set)
    family_candidates: dict[tuple[str, str], set[int]] = defaultdict(set)
    for id_model, row in reference_rows.items():
        model_key = normalize_text(row.get("MODEL"))
        exact_candidates[(normalize_text(row.get("MARKA")), model_key)].add(id_model)
        family_candidates[(normalize_brand_family(row.get("MARKA")), model_key)].add(id_model)
    exact_map = {
        key: next(iter(values)) for key, values in exact_candidates.items() if len(values) == 1
    }
    family_map = {
        key: next(iter(values)) for key, values in family_candidates.items() if len(values) == 1
    }
    return exact_map, family_map


def choose_reference_id(
    row: dict[str, Any],
    exact_map: dict[tuple[str, str], int],
    family_map: dict[tuple[str, str], int],
) -> tuple[int | None, str]:
    model_key = normalize_text(row.get("MODEL"))
    exact_key = (normalize_text(row.get("MARKA")), model_key)
    if exact_key in exact_map:
        return exact_map[exact_key], "exact"
    family_key = (normalize_brand_family(row.get("MARKA")), model_key)
    if family_key in family_map:
        return family_map[family_key], "family"
    return None, ""


def load_reference_counts(connection, table: str) -> dict[int, int]:
    cursor = connection.cursor()
    try:
        cursor.execute(
            f"SELECT ID_MODEL, COUNT(*) FROM {table} WHERE ID_MODEL IS NOT NULL GROUP BY ID_MODEL"
        )
        return {int(row[0]): int(row[1]) for row in cursor.fetchall() if row[0] is not None}
    finally:
        cursor.close()


def rows_differ(left: dict[str, Any], right: dict[str, Any], columns: list[str]) -> bool:
    for column in columns:
        if column == "ID_MODEL":
            continue
        if left.get(column) != right.get(column):
            return True
    return False


def build_extra_plans(
    current_rows: dict[int, dict[str, Any]],
    reference_rows: dict[int, dict[str, Any]],
    current_ref_counts: dict[str, dict[int, int]],
) -> list[ExtraModelPlan]:
    reference_ids = set(reference_rows)
    exact_map, family_map = build_reference_maps(reference_rows)
    plans: list[ExtraModelPlan] = []
    for id_model, row in sorted(current_rows.items()):
        if id_model in reference_ids:
            continue
        target_id, reason = choose_reference_id(row, exact_map, family_map)
        target = reference_rows.get(target_id, {})
        refs_maszyna = current_ref_counts["MASZYNA"].get(id_model, 0)
        refs_magazyn = current_ref_counts["MAGAZYN"].get(id_model, 0)
        refs_cennik = current_ref_counts["CENNIK"].get(id_model, 0)
        refs_mz = current_ref_counts["MZ"].get(id_model, 0)
        refs_total = refs_maszyna + refs_magazyn + refs_cennik + refs_mz
        if target_id is not None:
            action = "remap_delete" if refs_total else "delete"
        elif refs_total:
            action = "unresolved_referenced"
        else:
            action = "delete"
        plans.append(
            ExtraModelPlan(
                id_model=id_model,
                marka=str(row.get("MARKA") or "").strip(),
                model=str(row.get("MODEL") or "").strip(),
                refs_maszyna=refs_maszyna,
                refs_magazyn=refs_magazyn,
                refs_cennik=refs_cennik,
                refs_mz=refs_mz,
                target_id_model=target_id,
                target_marka=str(target.get("MARKA") or "").strip(),
                target_model=str(target.get("MODEL") or "").strip(),
                match_reason=reason,
                action=action,
            )
        )
    return plans


def backup_database(database_path: Path, report_dir: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_path = (
        report_dir / f"{database_path.stem}_before_model_repair_{stamp}{database_path.suffix}"
    )
    shutil.copy2(database_path, backup_path)
    return backup_path


def write_reports(
    *,
    report_dir: Path,
    extra_plans: list[ExtraModelPlan],
    summary: dict[str, Any],
) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    csv_path = report_dir / f"firebird_model_repair_{stamp}.csv"
    json_path = report_dir / f"firebird_model_repair_{stamp}.json"
    latest_csv = report_dir / "firebird_model_repair_latest.csv"
    latest_json = report_dir / "firebird_model_repair_latest.json"

    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "id_model",
                "marka",
                "model",
                "refs_maszyna",
                "refs_magazyn",
                "refs_cennik",
                "refs_mz",
                "refs_total",
                "target_id_model",
                "target_marka",
                "target_model",
                "match_reason",
                "action",
            ],
            delimiter=";",
        )
        writer.writeheader()
        for item in extra_plans:
            writer.writerow(
                {
                    "id_model": item.id_model,
                    "marka": item.marka,
                    "model": item.model,
                    "refs_maszyna": item.refs_maszyna,
                    "refs_magazyn": item.refs_magazyn,
                    "refs_cennik": item.refs_cennik,
                    "refs_mz": item.refs_mz,
                    "refs_total": item.refs_total,
                    "target_id_model": item.target_id_model or "",
                    "target_marka": item.target_marka,
                    "target_model": item.target_model,
                    "match_reason": item.match_reason,
                    "action": item.action,
                }
            )
    latest_csv.write_text(csv_path.read_text(encoding="utf-8"), encoding="utf-8")

    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_json.write_text(json_path.read_text(encoding="utf-8"), encoding="utf-8")
    return csv_path, json_path


def update_reference_tables(connection, remap_pairs: list[tuple[int, int]]) -> None:
    if not remap_pairs:
        return
    cursor = connection.cursor()
    try:
        for source_id, target_id in remap_pairs:
            for table in REFERENCE_TABLES:
                cursor.execute(
                    f"UPDATE {table} SET ID_MODEL = ? WHERE ID_MODEL = ?", (target_id, source_id)
                )
    finally:
        cursor.close()


def upsert_reference_rows(
    connection,
    *,
    columns: list[str],
    reference_rows: dict[int, dict[str, Any]],
    current_rows: dict[int, dict[str, Any]],
) -> tuple[int, int]:
    insert_sql = (
        f"INSERT INTO MODEL ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})"
    )
    update_columns = [column for column in columns if column != "ID_MODEL"]
    update_sql = f"UPDATE MODEL SET {', '.join(f'{column} = ?' for column in update_columns)} WHERE ID_MODEL = ?"
    cursor = connection.cursor()
    inserted = 0
    updated = 0
    try:
        for id_model, ref_row in sorted(reference_rows.items()):
            if id_model not in current_rows:
                cursor.execute(insert_sql, tuple(ref_row[column] for column in columns))
                inserted += 1
                continue
            if rows_differ(current_rows[id_model], ref_row, columns):
                params = [ref_row[column] for column in update_columns]
                params.append(id_model)
                cursor.execute(update_sql, tuple(params))
                updated += 1
    finally:
        cursor.close()
    return inserted, updated


def delete_extra_rows(connection, extra_ids: list[int]) -> int:
    if not extra_ids:
        return 0
    cursor = connection.cursor()
    deleted = 0
    try:
        for id_model in extra_ids:
            cursor.execute("DELETE FROM MODEL WHERE ID_MODEL = ?", (id_model,))
            deleted += 1
    finally:
        cursor.close()
    return deleted


def fingerprint_rows(rows: dict[int, dict[str, Any]], columns: list[str]) -> str:
    hash_state = hashlib.sha256()
    for id_model in sorted(rows):
        row = rows[id_model]
        payload = []
        for column in columns:
            value = row.get(column)
            payload.append("" if value is None else str(value))
        hash_state.update(("\x1f".join(payload) + "\n").encode("utf-8"))
    return hash_state.hexdigest()


def count_orphan_references(connection, table: str) -> int:
    cursor = connection.cursor()
    try:
        cursor.execute(
            f"""
            SELECT COUNT(*)
            FROM {table} t
            WHERE t.ID_MODEL IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM MODEL m
                  WHERE m.ID_MODEL = t.ID_MODEL
              )
            """
        )
        return int(cursor.fetchone()[0])
    finally:
        cursor.close()


def build_summary(
    *,
    database: str,
    reference_database: str,
    columns: list[str],
    current_rows: dict[int, dict[str, Any]],
    reference_rows: dict[int, dict[str, Any]],
    extra_plans: list[ExtraModelPlan],
    inserted: int = 0,
    updated: int = 0,
    deleted: int = 0,
    remapped: int = 0,
    backup_path: str | None = None,
) -> dict[str, Any]:
    unresolved = [item for item in extra_plans if item.action == "unresolved_referenced"]
    return {
        "database": database,
        "reference_database": reference_database,
        "model_before": len(current_rows),
        "model_reference": len(reference_rows),
        "extra_rows": len(extra_plans),
        "to_delete": sum(1 for item in extra_plans if item.action == "delete"),
        "to_remap_delete": sum(1 for item in extra_plans if item.action == "remap_delete"),
        "unresolved_referenced": len(unresolved),
        "inserted": inserted,
        "updated": updated,
        "deleted": deleted,
        "remapped": remapped,
        "backup_path": backup_path,
        "current_fingerprint": fingerprint_rows(current_rows, columns),
        "reference_fingerprint": fingerprint_rows(reference_rows, columns),
        "unresolved_examples": [
            {
                "id_model": item.id_model,
                "marka": item.marka,
                "model": item.model,
                "refs_total": item.refs_total,
            }
            for item in unresolved[:20]
        ],
    }


def main() -> int:
    args = parse_args()
    report_dir = _resolve_repo_path(args.report_dir)
    database_value, database_local_path = resolve_database_value(args.database)
    reference_database_value, reference_database_local_path = resolve_database_value(
        args.reference_database
    )

    if database_local_path is not None and not database_local_path.exists():
        print(f"Blad: brak bazy docelowej: {database_local_path}", file=sys.stderr)
        return 2
    if reference_database_local_path is None or not reference_database_local_path.exists():
        print(f"Blad: brak snapshotu referencyjnego: {reference_database_value}", file=sys.stderr)
        return 2

    reference_connection = connect_firebird(
        host=args.reference_host,
        port=args.reference_port,
        database=reference_database_value,
        user=args.user,
        password=args.password,
        charset=args.charset,
    )
    current_connection = connect_firebird(
        host=args.host,
        port=args.port,
        database=database_value,
        user=args.user,
        password=args.password,
        charset=args.charset,
    )
    try:
        columns = load_columns(reference_connection, "MODEL")
        reference_rows = load_rows_by_id(reference_connection, "MODEL", columns, "ID_MODEL")
        current_rows = load_rows_by_id(current_connection, "MODEL", columns, "ID_MODEL")
        current_ref_counts = {
            table: load_reference_counts(current_connection, table) for table in REFERENCE_TABLES
        }
        extra_plans = build_extra_plans(current_rows, reference_rows, current_ref_counts)
        summary = build_summary(
            database=database_value,
            reference_database=reference_database_value,
            columns=columns,
            current_rows=current_rows,
            reference_rows=reference_rows,
            extra_plans=extra_plans,
        )
        csv_path, json_path = write_reports(
            report_dir=report_dir, extra_plans=extra_plans, summary=summary
        )
        print(f"Raport CSV: {csv_path}")
        print(f"Raport JSON: {json_path}")

        unresolved = [item for item in extra_plans if item.action == "unresolved_referenced"]
        if not args.apply:
            print(
                "Dry-run zakonczony. "
                f"Extra MODEL: {summary['extra_rows']}, do usuniecia: {summary['to_delete']}, "
                f"do przepiecia i usuniecia: {summary['to_remap_delete']}, nierozwiazane: {len(unresolved)}"
            )
            return 0

        if unresolved:
            print(
                "Blad: sa rekordy MODEL z referencjami bez jednoznacznego mapowania.",
                file=sys.stderr,
            )
            return 3

        backup_path: Path | None = None
        if not args.skip_backup:
            if database_local_path is None:
                print(
                    "Blad: backup plikowy jest obslugiwany tylko dla lokalnej bazy docelowej. "
                    "Dla zdalnej bazy wykonaj backup osobno i uruchom --skip-backup.",
                    file=sys.stderr,
                )
                return 4
            backup_path = backup_database(database_local_path, report_dir)
        remap_pairs = sorted(
            {
                (item.id_model, item.target_id_model)
                for item in extra_plans
                if item.action == "remap_delete" and item.target_id_model is not None
            }
        )
        update_reference_tables(current_connection, remap_pairs)
        deleted = delete_extra_rows(
            current_connection,
            [item.id_model for item in extra_plans if item.action in {"delete", "remap_delete"}],
        )
        current_rows_after_delete = load_rows_by_id(
            current_connection, "MODEL", columns, "ID_MODEL"
        )
        inserted, updated = upsert_reference_rows(
            current_connection,
            columns=columns,
            reference_rows=reference_rows,
            current_rows=current_rows_after_delete,
        )
        current_connection.commit()

        repaired_rows = load_rows_by_id(current_connection, "MODEL", columns, "ID_MODEL")
        validation = {
            "model_after": len(repaired_rows),
            "matches_reference": repaired_rows == reference_rows,
            "fingerprint_after": fingerprint_rows(repaired_rows, columns),
            "fingerprint_reference": fingerprint_rows(reference_rows, columns),
            "orphan_refs": {
                table: count_orphan_references(current_connection, table)
                for table in REFERENCE_TABLES
            },
        }
        summary = build_summary(
            database=database_value,
            reference_database=reference_database_value,
            columns=columns,
            current_rows=repaired_rows,
            reference_rows=reference_rows,
            extra_plans=extra_plans,
            inserted=inserted,
            updated=updated,
            deleted=deleted,
            remapped=len(remap_pairs),
            backup_path=str(backup_path) if backup_path is not None else None,
        )
        summary["validation"] = validation
        _, json_path = write_reports(
            report_dir=report_dir, extra_plans=extra_plans, summary=summary
        )
        if backup_path is not None:
            print(f"Backup bazy: {backup_path}")
        else:
            print("Backup bazy: pominieto w skrypcie, poniewaz wykonano go osobno.")
        print(
            f"Zastosowano naprawe: inserted={inserted}, updated={updated}, "
            f"remapped={len(remap_pairs)}, deleted={deleted}"
        )
        print(
            "Walidacja: "
            f"matches_reference={validation['matches_reference']}, "
            f"orphan_refs={validation['orphan_refs']}"
        )
        print(f"Raport JSON po apply: {json_path}")
        return 0
    finally:
        current_connection.close()
        reference_connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
