#!/usr/bin/env python3
"""Synchronizuje MODEL.PLIK z finalnymi obrazami w katalogu imgdev.

Skrypt jest przygotowany do pracy na lokalnej kopii Firebird wskazanej przez
`FB_LOCAL_COPY_PATH`. Domyslnie laczy sie przez `firebirdsql` do `127.0.0.1`
na porcie `FB_PORT`, generuje raport CSV/MD i dopiero po podaniu `--apply`
zapisuje zmiany w tabeli `MODEL`.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

RICOH_FAMILY = {"RICOH", "NASHUATEC", "GESTETNER", "LANIER", "INFOTEC"}
OUTPUT_PREFIX = "ran_"
DEFAULT_BASE_URL = "https://ksero-partner.com.pl/imgdev/"
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IMGDEV_DIR = REPO_ROOT / "inbox" / "audyt_model" / "imgdev"
DEFAULT_REPORT_DIR = REPO_ROOT / "inbox" / "audyt_model"
DEFAULT_DATABASE = os.getenv("FB_LOCAL_COPY_PATH", "inbox/firebird/menadzer_serwisu.fdb")
DEFAULT_DATABASE_ALIAS = os.getenv("FB_LOCAL_COPY_ALIAS", "BAZAMS_TEST")
DEFAULT_MODE = os.getenv("FB_MODE", "local").strip().lower()
DEFAULT_PORT = int(os.getenv("FB_PORT", "3050"))
DEFAULT_USER = os.getenv("FB_USER", "SYSDBA")
DEFAULT_PASSWORD = os.getenv("FB_PASSWORD", "masterkey")
DEFAULT_CHARSET = os.getenv("FB_CHARSET", "UTF8")


@dataclass(frozen=True, slots=True)
class ModelRow:
    id_model: int
    marka: str
    model: str
    plik: str


@dataclass(frozen=True, slots=True)
class PlannedChange:
    id_model: int
    marka: str
    model: str
    plik_old: str
    plik_new: str
    target_file: str
    status: str
    reason: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Synchronizuje MODEL.PLIK z finalnymi obrazami w inbox/audyt_model/imgdev. "
            "Domyslnie wykonuje dry-run i zapisuje raport."
        )
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="Host lokalnego Firebird.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port lokalnego Firebird.")
    parser.add_argument(
        "--database",
        default=DEFAULT_DATABASE,
        help="Sciezka lokalnej kopii Firebird albo alias widoczny dla lokalnego serwera.",
    )
    parser.add_argument(
        "--database-alias",
        default=DEFAULT_DATABASE_ALIAS,
        help="Alias lokalnej kopii Firebird uzywany, gdy serwer nie widzi sciezki WSL.",
    )
    parser.add_argument("--user", default=DEFAULT_USER, help="Uzytkownik Firebird.")
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="Haslo Firebird.")
    parser.add_argument("--charset", default=DEFAULT_CHARSET, help="Charset polaczenia.")
    parser.add_argument(
        "--imgdev-dir",
        type=Path,
        default=DEFAULT_IMGDEV_DIR,
        help="Katalog z finalnymi obrazami.",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="Bazowy URL katalogu /imgdev/.",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=DEFAULT_REPORT_DIR,
        help="Katalog raportow CSV/MD.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Wykonaj UPDATE MODEL.PLIK. Bez tej flagi skrypt tylko generuje raport.",
    )
    return parser.parse_args()


def _resolve_repo_path(path_value: str | Path) -> Path:
    path = Path(path_value).expanduser()
    if path.is_absolute():
        return path
    return (REPO_ROOT / path).resolve()


def resolve_default_host(fb_mode: str | None, fb_host: str | None) -> str:
    if (fb_mode or "").strip().lower() == "local":
        return "127.0.0.1"
    return (fb_host or "127.0.0.1").strip() or "127.0.0.1"


DEFAULT_HOST = resolve_default_host(DEFAULT_MODE, os.getenv("FB_HOST", "127.0.0.1"))


def _looks_like_path(value: str) -> bool:
    normalized = (value or "").strip()
    if not normalized:
        return False
    return (
        "/" in normalized
        or "\\" in normalized
        or normalized.startswith(".")
        or bool(re.match(r"^[A-Za-z]:[/\\\\]", normalized))
    )


def build_database_candidates(database: str, database_alias: str | None) -> list[str]:
    raw_value = (database or "").strip()
    alias_value = (database_alias or "").strip()
    candidates: list[str] = []

    if _looks_like_path(raw_value):
        candidates.append(str(_resolve_repo_path(raw_value)))
        if alias_value and alias_value not in candidates:
            candidates.append(alias_value)
        return candidates

    if raw_value:
        candidates.append(raw_value)
    if alias_value and alias_value not in candidates:
        candidates.append(alias_value)
    return candidates


def slugify_model(model: str) -> str:
    slug = model.lower().replace("+", " plus ")
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    slug = re.sub(r"_+", "_", slug).strip("_")
    return f"ricoh_{slug}"


def build_target_file(model: str) -> str:
    return f"{OUTPUT_PREFIX}{slugify_model(model)}.png"


def build_target_url(model: str, imgdev_dir: Path, base_url: str) -> tuple[str, str] | None:
    target_file = build_target_file(model)
    if not (imgdev_dir / target_file).exists():
        return None
    normalized_base = base_url.rstrip("/") + "/"
    return normalized_base + target_file, target_file


def classify_row(row: ModelRow, imgdev_dir: Path, base_url: str) -> PlannedChange:
    marka = row.marka.strip()
    model = row.model.strip()
    plik = row.plik.strip()

    if marka.upper() not in RICOH_FAMILY:
        return PlannedChange(
            row.id_model, marka, model, plik, plik, "", "skip_family", "Marka poza rodzina Ricoh."
        )
    if not plik:
        return PlannedChange(
            row.id_model, marka, model, plik, plik, "", "skip_empty", "Brak wartosci PLIK."
        )

    target = build_target_url(model, imgdev_dir, base_url)
    if target is None:
        return PlannedChange(
            row.id_model,
            marka,
            model,
            plik,
            plik,
            build_target_file(model),
            "skip_missing_target",
            "Brak finalnego pliku w imgdev dla tego modelu.",
        )

    target_url, target_file = target
    current_file = Path(urlsplit(plik).path).name
    if plik == target_url:
        return PlannedChange(
            row.id_model,
            marka,
            model,
            plik,
            target_url,
            target_file,
            "already_ok",
            "PLIK juz wskazuje finalny URL.",
        )
    if current_file == target_file:
        return PlannedChange(
            row.id_model,
            marka,
            model,
            plik,
            target_url,
            target_file,
            "ready_update",
            "Unifikacja domeny albo schematu URL.",
        )
    return PlannedChange(
        row.id_model,
        marka,
        model,
        plik,
        target_url,
        target_file,
        "ready_update",
        "Znaleziono finalny plik po slugu modelu.",
    )


def load_rows(connection) -> list[ModelRow]:
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT ID_MODEL, MARKA, MODEL, PLIK FROM MODEL ORDER BY ID_MODEL")
        rows = []
        for id_model, marka, model, plik in cursor.fetchall():
            if id_model is None:
                continue
            rows.append(
                ModelRow(
                    id_model=int(id_model),
                    marka=(marka or "").strip(),
                    model=(model or "").strip(),
                    plik=(plik or "").strip(),
                )
            )
        return rows
    finally:
        try:
            cursor.close()
        except Exception:
            pass


def connect_firebird(
    *, host: str, port: int, databases: list[str], user: str, password: str, charset: str
):
    import firebirdsql  # type: ignore[import-not-found]

    last_error: Exception | None = None
    for database in databases:
        try:
            connection = firebirdsql.connect(
                host=host,
                port=port,
                database=database,
                user=user,
                password=password,
                charset=charset,
            )
            return connection, database
        except Exception as exc:  # pragma: no cover - zalezne od srodowiska
            last_error = exc
    assert last_error is not None
    raise last_error


def backup_database(database_path: Path, report_dir: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_path = (
        report_dir / f"{database_path.stem}_before_model_plik_sync_{stamp}{database_path.suffix}"
    )
    shutil.copy2(database_path, backup_path)
    return backup_path


def write_reports(changes: list[PlannedChange], report_dir: Path) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    csv_path = report_dir / f"firebird_model_plik_sync_{stamp}.csv"
    md_path = report_dir / f"firebird_model_plik_sync_{stamp}.md"
    latest_csv = report_dir / "firebird_model_plik_sync_latest.csv"
    latest_md = report_dir / "firebird_model_plik_sync_latest.md"

    rows = [
        {
            "id_model": item.id_model,
            "marka": item.marka,
            "model": item.model,
            "plik_old": item.plik_old,
            "plik_new": item.plik_new,
            "target_file": item.target_file,
            "status": item.status,
            "reason": item.reason,
        }
        for item in changes
    ]

    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "id_model",
                "marka",
                "model",
                "plik_old",
                "plik_new",
                "target_file",
                "status",
                "reason",
            ],
            delimiter=";",
        )
        writer.writeheader()
        writer.writerows(rows)
    latest_csv.write_text(csv_path.read_text(encoding="utf-8"), encoding="utf-8")

    ready = sum(1 for item in changes if item.status == "ready_update")
    already = sum(1 for item in changes if item.status == "already_ok")
    skipped = len(changes) - ready - already
    with md_path.open("w", encoding="utf-8") as handle:
        handle.write("# Synchronizacja MODEL.PLIK\n\n")
        handle.write(f"- data UTC: `{stamp}`\n")
        handle.write(f"- rekordy przeanalizowane: `{len(changes)}`\n")
        handle.write(f"- gotowe do aktualizacji: `{ready}`\n")
        handle.write(f"- juz poprawne: `{already}`\n")
        handle.write(f"- pominiete: `{skipped}`\n")
        handle.write(f"- szczegoly: `{csv_path.name}`\n")
    latest_md.write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")
    return csv_path, md_path


def apply_changes(connection, changes: list[PlannedChange]) -> int:
    to_update = [item for item in changes if item.status == "ready_update"]
    if not to_update:
        return 0

    cursor = connection.cursor()
    try:
        for item in to_update:
            cursor.execute(
                "UPDATE MODEL SET PLIK = ? WHERE ID_MODEL = ?", (item.plik_new, item.id_model)
            )
        connection.commit()
    finally:
        try:
            cursor.close()
        except Exception:
            pass
    return len(to_update)


def main() -> int:
    args = parse_args()
    imgdev_dir = _resolve_repo_path(args.imgdev_dir)
    report_dir = _resolve_repo_path(args.report_dir)
    database_candidates = build_database_candidates(args.database, args.database_alias)
    database_path = _resolve_repo_path(args.database) if _looks_like_path(args.database) else None

    if not imgdev_dir.exists():
        print(f"Blad: brak katalogu imgdev: {imgdev_dir}", file=sys.stderr)
        return 2
    if not database_candidates:
        print("Blad: brak docelowej bazy Firebird albo aliasu.", file=sys.stderr)
        return 2

    connection, connected_database = connect_firebird(
        host=args.host,
        port=args.port,
        databases=database_candidates,
        user=args.user,
        password=args.password,
        charset=args.charset,
    )
    try:
        print(f"Polaczono z Firebird przez: {connected_database}")
        rows = load_rows(connection)
        changes = [classify_row(row, imgdev_dir, args.base_url) for row in rows]
        csv_path, md_path = write_reports(changes, report_dir)

        print(f"Raport CSV: {csv_path}")
        print(f"Raport MD: {md_path}")

        if not args.apply:
            ready = sum(1 for item in changes if item.status == "ready_update")
            print(f"Dry-run zakonczony. Rekordy gotowe do aktualizacji: {ready}")
            return 0

        if database_path is not None and database_path.exists():
            backup_path = backup_database(database_path, report_dir)
            print(f"Backup lokalnej bazy: {backup_path}")
        updated = apply_changes(connection, changes)
        print(f"Zaktualizowano MODEL.PLIK: {updated}")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
