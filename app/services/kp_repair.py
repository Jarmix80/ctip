"""Operacje naprawcze dla oznaczeń KP/xxxx w polu MASZYNA.EWIDENCJA."""

from __future__ import annotations

import csv
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import firebirdsql  # type: ignore[import-not-found]

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = REPO_ROOT / "inbox" / "ewidencja"

MARKER_ORDER = ("V", "E", "R")
LEGACY_MARKER_MAP: dict[str, str] = {
    "REMOTE": "R",
    "R": "R",
    "EMAIL": "E",
    "E": "E",
    "VM": "V",
    "V": "V",
    "VMAINT": "V",
    "VMAINTENANCE": "V",
}
SERIAL_COLUMNS = (
    "Device Serial Number",
    "Appliance Serial Number",
    "Serial Number",
    "SERIAL",
    "NR_SERYJNY",
    "ID_NR_SERYJNY",
)

WINDOWS_PATH_PATTERN = re.compile(r"^(?P<drive>[A-Za-z]):[\\/](?P<rest>.*)$")
SEGMENT_CLEAN_PATTERN = re.compile(r"[^A-Z0-9]+")


@dataclass(slots=True)
class FirebirdConnConfig:
    """Parametry połączenia Firebird."""

    host: str
    port: int
    database: str
    user: str
    password: str | None
    charset: str = "WIN1250"
    role: str | None = None


@dataclass(slots=True)
class KpRepairSourceConfig:
    """Konfiguracja źródeł wejściowych naprawy KP."""

    csv_directory: str
    csv_pattern: str
    email_lookback_months: int


@dataclass(slots=True)
class DeviceRow:
    """Minimalny widok rekordu urządzenia w bazie głównej."""

    id_maszyna: int
    serial: str
    serial2: str
    ewidencja: str | None


@dataclass(slots=True)
class RepairUpdate:
    """Pojedyncza zmiana EWIDENCJA."""

    id_maszyna: int
    serial: str
    old_ewidencja: str | None
    new_ewidencja: str | None
    marker_v: bool
    marker_e: bool
    marker_r: bool


@dataclass(slots=True)
class SourceSnapshot:
    """Stan źródeł wejściowych użytych do tagowania."""

    v_serials: set[str]
    csv_serials: set[str]
    email_device_ids: set[int]
    email_serials: set[str]
    source_counts: dict[str, int]
    latest_csv_file: str | None


@dataclass(slots=True)
class CsvSourceTestResult:
    """Rezultat testu katalogu CSV."""

    success: bool
    message: str
    directory_exists: bool
    files_found: int
    latest_file: str | None


@dataclass(slots=True)
class SummaryResult:
    """Podsumowanie źródeł i bieżących markerów."""

    marker_counts: dict[str, int]
    source_counts: dict[str, int]
    matched_counts: dict[str, int]
    latest_csv_file: str | None
    report_file: str


@dataclass(slots=True)
class ActionResult:
    """Wynik operacji czyszczenia/retagowania."""

    success: bool
    message: str
    commit: bool
    candidates: int
    updated: int
    skipped: int
    errors: int
    marker_counts_before: dict[str, int]
    marker_counts_after: dict[str, int]
    source_counts: dict[str, int] | None
    report_file: str
    map_file: str
    rollback_file: str


def _connect_firebird(config: FirebirdConnConfig):
    kwargs: dict[str, object] = {
        "host": config.host.strip(),
        "port": int(config.port),
        "database": config.database.strip(),
        "user": config.user.strip(),
        "password": config.password or "",
        "charset": (config.charset or "WIN1250").strip() or "WIN1250",
    }
    role = (config.role or "").strip()
    if role:
        kwargs["role"] = role
    return firebirdsql.connect(**kwargs)


def _normalize_serial(value: str | None) -> str:
    if value is None:
        return ""
    return str(value).strip().upper()


def _segment_key(segment: str) -> str:
    return SEGMENT_CLEAN_PATTERN.sub("", segment.upper())


def _segment_marker(segment: str) -> str | None:
    key = _segment_key(segment)
    if not key:
        return None
    if key in LEGACY_MARKER_MAP:
        return LEGACY_MARKER_MAP[key]
    if key.startswith("REMOTE"):
        return "R"
    if key.startswith("EMAIL"):
        return "E"
    if key.startswith("VMAINT"):
        return "V"
    return None


def _split_segments(value: str | None) -> list[str]:
    if value is None:
        return []
    normalized = str(value).replace("\\", "/")
    return [part.strip() for part in normalized.split("/") if part and part.strip()]


def _clean_ewidencja(value: str | None) -> str | None:
    if value is None:
        return None
    segments = _split_segments(value)
    if not segments:
        return None
    filtered = [part for part in segments if _segment_marker(part) is None]
    if not filtered:
        return None
    joined = "/".join(filtered)
    needs_trailing = (
        str(value).strip().endswith(("/", "\\")) or _segment_marker(segments[-1]) is not None
    )
    if needs_trailing:
        joined = f"{joined}/"
    return joined[:100]


def _compose_ewidencja(base_value: str | None, marker_set: set[str]) -> str | None:
    base_segments = _split_segments(base_value)
    for marker in MARKER_ORDER:
        if marker in marker_set:
            base_segments.append(marker)
    if not base_segments:
        return None
    joined = "/".join(base_segments)
    if marker_set:
        joined = f"{joined}/"
    return joined[:100]


def _marker_presence(value: str | None) -> set[str]:
    markers: set[str] = set()
    for segment in _split_segments(value):
        marker = _segment_marker(segment)
        if marker in {"R", "E", "V"}:
            markers.add(marker)
    return markers


def _marker_counts(rows: Iterable[DeviceRow]) -> dict[str, int]:
    counts = {"V": 0, "E": 0, "R": 0}
    for row in rows:
        for marker in _marker_presence(row.ewidencja):
            counts[marker] += 1
    return counts


def _subtract_months(base_date: date, months: int) -> date:
    if months <= 0:
        return date(1900, 1, 1)
    year = base_date.year
    month = base_date.month - months
    while month <= 0:
        month += 12
        year -= 1
    day = min(base_date.day, _month_last_day(year, month))
    return date(year, month, day)


def _month_last_day(year: int, month: int) -> int:
    if month == 12:
        first_next = date(year + 1, 1, 1)
    else:
        first_next = date(year, month + 1, 1)
    return (first_next - date.resolution).day


def _resolve_csv_directory(path_value: str) -> Path:
    normalized = path_value.strip()
    matched = WINDOWS_PATH_PATTERN.match(normalized)
    if matched and os.name != "nt":
        drive = matched.group("drive").lower()
        rest = matched.group("rest").replace("\\", "/")
        return (Path("/mnt") / drive / rest).expanduser()
    return Path(normalized).expanduser()


def _collect_csv_files(directory: Path, pattern: str) -> list[Path]:
    if not directory.exists() or not directory.is_dir():
        return []
    glob_pattern = pattern.strip() or "DPLAC*.csv"
    files = [path for path in directory.glob(glob_pattern) if path.is_file()]
    files.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return files


def test_csv_source(config: KpRepairSourceConfig) -> CsvSourceTestResult:
    """Sprawdza dostępność katalogu CSV i obecność plików wejściowych."""
    directory = _resolve_csv_directory(config.csv_directory)
    exists = directory.exists() and directory.is_dir()
    if not exists:
        return CsvSourceTestResult(
            success=False,
            message=f"Katalog nie istnieje: {directory}",
            directory_exists=False,
            files_found=0,
            latest_file=None,
        )

    files = _collect_csv_files(directory, config.csv_pattern)
    latest_file = str(files[0]) if files else None
    if not files:
        return CsvSourceTestResult(
            success=False,
            message=(
                f"Nie znaleziono plików CSV w katalogu {directory} "
                f"dla wzorca {config.csv_pattern!r}."
            ),
            directory_exists=True,
            files_found=0,
            latest_file=None,
        )
    return CsvSourceTestResult(
        success=True,
        message=f"Znaleziono {len(files)} plik(ów) CSV. Najnowszy: {files[0].name}",
        directory_exists=True,
        files_found=len(files),
        latest_file=latest_file,
    )


def _read_csv_serials(config: KpRepairSourceConfig) -> tuple[set[str], int, str | None]:
    directory = _resolve_csv_directory(config.csv_directory)
    files = _collect_csv_files(directory, config.csv_pattern)
    if not files:
        return set(), 0, None
    selected = files[0]

    def _open_reader(path: Path) -> tuple[csv.DictReader, object]:
        for encoding in ("utf-8-sig", "cp1250", "latin1"):
            try:
                handle = path.open("r", encoding=encoding, newline="", errors="replace")
                return csv.DictReader(handle), handle
            except OSError:
                continue
        raise OSError(f"Nie można otworzyć pliku CSV: {path}")

    reader, handle = _open_reader(selected)
    rows = 0
    serials: set[str] = set()
    try:
        columns = reader.fieldnames or []
        serial_column = None
        normalized = {col.strip().lower(): col for col in columns if col}
        for candidate in SERIAL_COLUMNS:
            key = candidate.strip().lower()
            if key in normalized:
                serial_column = normalized[key]
                break
        if serial_column is None and columns:
            serial_column = columns[0]

        for row in reader:
            rows += 1
            if not serial_column:
                continue
            serial = _normalize_serial(row.get(serial_column))
            if serial:
                serials.add(serial)
    finally:
        handle.close()
    return serials, rows, str(selected)


def _to_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def _load_email_snapshot(
    main_config: FirebirdConnConfig,
    lookback_months: int,
) -> tuple[set[int], set[str], dict[str, int]]:
    device_ids: set[int] = set()
    serials: set[str] = set()
    counts = {
        "EMAIL_LATEST_TOTAL": 0,
        "EMAIL_REAL_ALL": 0,
        "EMAIL_REAL_LOOKBACK": 0,
        "CSV_REMOTE_LATEST": 0,
        "IMPORT_OTHER_LATEST": 0,
    }

    cutoff = _subtract_months(datetime.now(UTC).date(), lookback_months)
    con = _connect_firebird(main_config)
    cur = con.cursor()
    try:
        cur.execute(
            """
            SELECT
                c.ID_DEVICE,
                TRIM(COALESCE(c.SERIAL, '')),
                TRIM(COALESCE(c.MAILFROM, '')),
                c.COUNTER_DATE,
                c.ID_CMAIL
            FROM CMAIL c
            WHERE c.ID_DEVICE IS NOT NULL
              AND c.ID_CMAIL = (
                  SELECT FIRST 1 c2.ID_CMAIL
                  FROM CMAIL c2
                  WHERE c2.ID_DEVICE = c.ID_DEVICE
                  ORDER BY c2.COUNTER_DATE DESC NULLS LAST, c2.ID_CMAIL DESC
              )
            """
        )
        for row in cur.fetchall():
            counts["EMAIL_LATEST_TOTAL"] += 1
            id_device = int(row[0]) if row[0] is not None else None
            serial = _normalize_serial(row[1])
            mailfrom = (row[2] or "").strip()
            counter_date = _to_date(row[3])

            mailfrom_upper = mailfrom.upper()
            is_import = mailfrom_upper.startswith("[IMPORT]")
            is_csv_remote = is_import and ("REMOTE" in mailfrom_upper or "CSV" in mailfrom_upper)
            if is_csv_remote:
                counts["CSV_REMOTE_LATEST"] += 1
                continue
            if is_import:
                counts["IMPORT_OTHER_LATEST"] += 1
                continue
            if not mailfrom:
                continue

            counts["EMAIL_REAL_ALL"] += 1
            if counter_date is None or counter_date < cutoff:
                continue

            counts["EMAIL_REAL_LOOKBACK"] += 1
            if id_device is not None:
                device_ids.add(id_device)
            if serial:
                serials.add(serial)
    finally:
        cur.close()
        con.close()

    return device_ids, serials, counts


def _load_v_serials(v_config: FirebirdConnConfig) -> set[str]:
    serials: set[str] = set()
    con = _connect_firebird(v_config)
    cur = con.cursor()
    try:
        cur.execute(
            """
            SELECT TRIM(COALESCE(ID_NR_SERYJNY, ''))
            FROM MASZYNY
            WHERE TRIM(COALESCE(ID_NR_SERYJNY, '')) <> ''
              AND UPPER(TRIM(COALESCE(SERWISOWANA, 'TAK'))) = 'TAK'
            """
        )
        for row in cur.fetchall():
            serial = _normalize_serial(row[0] if row else "")
            if serial:
                serials.add(serial)
    finally:
        cur.close()
        con.close()
    return serials


def build_source_snapshot(
    *,
    main_config: FirebirdConnConfig,
    v_config: FirebirdConnConfig,
    source_config: KpRepairSourceConfig,
    email_lookback_months: int | None = None,
) -> SourceSnapshot:
    """Buduje komplet źródeł do retagowania KP."""
    lookback = (
        source_config.email_lookback_months
        if email_lookback_months is None
        else max(0, int(email_lookback_months))
    )
    v_serials = _load_v_serials(v_config)
    csv_serials, csv_rows, latest_csv_file = _read_csv_serials(source_config)
    email_device_ids, email_serials, email_counts = _load_email_snapshot(main_config, lookback)

    source_counts = {
        "V_SERIALS": len(v_serials),
        "CSV_ROWS": csv_rows,
        "CSV_SERIALS": len(csv_serials),
        "EMAIL_DEVICE_IDS": len(email_device_ids),
        "EMAIL_SERIALS": len(email_serials),
        "EMAIL_LOOKBACK_MONTHS": lookback,
        **email_counts,
    }
    return SourceSnapshot(
        v_serials=v_serials,
        csv_serials=csv_serials,
        email_device_ids=email_device_ids,
        email_serials=email_serials,
        source_counts=source_counts,
        latest_csv_file=latest_csv_file,
    )


def load_main_devices(config: FirebirdConnConfig) -> list[DeviceRow]:
    """Pobiera minimalny zestaw danych MASZYNA potrzebny do operacji KP."""
    rows: list[DeviceRow] = []
    con = _connect_firebird(config)
    cur = con.cursor()
    try:
        cur.execute(
            """
            SELECT
                ID_MASZYNA,
                TRIM(COALESCE(SERIAL, '')),
                TRIM(COALESCE(SERIAL2, '')),
                EWIDENCJA
            FROM MASZYNA
            """
        )
        for row in cur.fetchall():
            rows.append(
                DeviceRow(
                    id_maszyna=int(row[0]),
                    serial=_normalize_serial(row[1]),
                    serial2=_normalize_serial(row[2]),
                    ewidencja=(str(row[3]).strip() if row[3] is not None else None),
                )
            )
    finally:
        cur.close()
        con.close()
    return rows


def _row_serial(row: DeviceRow) -> str:
    return row.serial or row.serial2


def _row_markers(row: DeviceRow, snapshot: SourceSnapshot) -> set[str]:
    serial_candidates = {value for value in (row.serial, row.serial2) if value}
    markers: set[str] = set()
    if serial_candidates.intersection(snapshot.v_serials):
        markers.add("V")
    if serial_candidates.intersection(snapshot.csv_serials):
        markers.add("R")
    if row.id_maszyna in snapshot.email_device_ids or serial_candidates.intersection(
        snapshot.email_serials
    ):
        markers.add("E")
    return markers


def _prepare_clear_updates(rows: Iterable[DeviceRow]) -> list[RepairUpdate]:
    updates: list[RepairUpdate] = []
    for row in rows:
        new_ewidencja = _clean_ewidencja(row.ewidencja)
        if (row.ewidencja or "") == (new_ewidencja or ""):
            continue
        updates.append(
            RepairUpdate(
                id_maszyna=row.id_maszyna,
                serial=_row_serial(row),
                old_ewidencja=row.ewidencja,
                new_ewidencja=new_ewidencja,
                marker_v=False,
                marker_e=False,
                marker_r=False,
            )
        )
    return updates


def _prepare_rebuild_updates(
    rows: Iterable[DeviceRow],
    snapshot: SourceSnapshot,
) -> list[RepairUpdate]:
    updates: list[RepairUpdate] = []
    for row in rows:
        marker_set = _row_markers(row, snapshot)
        base = _clean_ewidencja(row.ewidencja)
        new_ewidencja = _compose_ewidencja(base, marker_set)
        if (row.ewidencja or "") == (new_ewidencja or ""):
            continue
        updates.append(
            RepairUpdate(
                id_maszyna=row.id_maszyna,
                serial=_row_serial(row),
                old_ewidencja=row.ewidencja,
                new_ewidencja=new_ewidencja,
                marker_v="V" in marker_set,
                marker_e="E" in marker_set,
                marker_r="R" in marker_set,
            )
        )
    return updates


def _sql_literal(value: str | None) -> str:
    if value is None:
        return "NULL"
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def _timestamp_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d_%H%M%S_UTC")


def _write_map_file(path: Path, updates: Iterable[RepairUpdate]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "id_maszyna",
                "serial",
                "ewidencja_stara",
                "ewidencja_nowa",
                "marker_v",
                "marker_e",
                "marker_r",
            ]
        )
        for item in updates:
            writer.writerow(
                [
                    item.id_maszyna,
                    item.serial,
                    item.old_ewidencja or "",
                    item.new_ewidencja or "",
                    "1" if item.marker_v else "0",
                    "1" if item.marker_e else "0",
                    "1" if item.marker_r else "0",
                ]
            )


def _write_rollback_file(path: Path, updates: Iterable[RepairUpdate], *, title: str) -> None:
    with path.open("w", encoding="utf-8") as handle:
        handle.write(f"-- {title}\n")
        handle.write(f"-- Timestamp UTC: {_timestamp_utc()}\n")
        handle.write("SET AUTODDL OFF;\n")
        handle.write("COMMIT;\n\n")
        for item in updates:
            old_lit = _sql_literal(item.old_ewidencja)
            new_lit = _sql_literal(item.new_ewidencja)
            handle.write(
                "UPDATE MASZYNA "
                f"SET EWIDENCJA = {old_lit} "
                f"WHERE ID_MASZYNA = {item.id_maszyna} "
                f"AND COALESCE(EWIDENCJA, '') = COALESCE({new_lit}, '');\n"
            )
        handle.write("\nCOMMIT;\n")


def _write_report(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def _apply_updates(
    config: FirebirdConnConfig,
    updates: Iterable[RepairUpdate],
    *,
    commit: bool,
) -> tuple[int, int, int]:
    candidates = list(updates)
    if not commit:
        return 0, len(candidates), 0
    if not candidates:
        return 0, 0, 0

    con = _connect_firebird(config)
    cur = con.cursor()
    updated = 0
    skipped = 0
    try:
        for item in candidates:
            cur.execute(
                """
                UPDATE MASZYNA
                SET EWIDENCJA = ?
                WHERE ID_MASZYNA = ?
                  AND COALESCE(EWIDENCJA, '') = COALESCE(?, '')
                """,
                (item.new_ewidencja, item.id_maszyna, item.old_ewidencja),
            )
            if cur.rowcount:
                updated += int(cur.rowcount)
            else:
                skipped += 1
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        cur.close()
        con.close()
    return updated, skipped, 0


def build_summary(
    *,
    main_config: FirebirdConnConfig,
    v_config: FirebirdConnConfig,
    source_config: KpRepairSourceConfig,
    email_lookback_months: int | None = None,
) -> SummaryResult:
    """Buduje raport ilości /V /E /R oraz źródeł danych."""
    snapshot = build_source_snapshot(
        main_config=main_config,
        v_config=v_config,
        source_config=source_config,
        email_lookback_months=email_lookback_months,
    )
    rows = load_main_devices(main_config)
    marker_counts = _marker_counts(rows)

    matched_counts = {"V": 0, "E": 0, "R": 0}
    for row in rows:
        marker_set = _row_markers(row, snapshot)
        for marker in marker_set:
            matched_counts[marker] += 1

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = _timestamp_utc()
    report_path = REPORT_DIR / f"raport_kp_summary_{stamp}.md"
    lines = [
        "# Raport Naprawa KP/xxxx",
        "",
        f"Czas UTC: {stamp}",
        "",
        "## Aktualne markery w MASZYNA.EWIDENCJA",
        f"- V: {marker_counts['V']}",
        f"- E: {marker_counts['E']}",
        f"- R: {marker_counts['R']}",
        "",
        "## Źródła wejściowe",
        f"- V seriale (v-maintenance): {snapshot.source_counts['V_SERIALS']}",
        f"- CSV wiersze: {snapshot.source_counts['CSV_ROWS']}",
        f"- CSV seriale: {snapshot.source_counts['CSV_SERIALS']}",
        f"- EMAIL (ID_DEVICE): {snapshot.source_counts['EMAIL_DEVICE_IDS']}",
        f"- EMAIL (SERIAL): {snapshot.source_counts['EMAIL_SERIALS']}",
        f"- EMAIL lookback [miesiące]: {snapshot.source_counts['EMAIL_LOOKBACK_MONTHS']}",
        f"- CMAIL latest total: {snapshot.source_counts['EMAIL_LATEST_TOTAL']}",
        f"- CMAIL EMAIL_REAL all: {snapshot.source_counts['EMAIL_REAL_ALL']}",
        f"- CMAIL EMAIL_REAL lookback: {snapshot.source_counts['EMAIL_REAL_LOOKBACK']}",
        f"- CMAIL CSV_REMOTE latest: {snapshot.source_counts['CSV_REMOTE_LATEST']}",
        f"- CMAIL IMPORT_INNE latest: {snapshot.source_counts['IMPORT_OTHER_LATEST']}",
        f"- Najnowszy CSV: {snapshot.latest_csv_file or 'brak'}",
        "",
        "## Dopasowania do MASZYNA",
        f"- Kandydaci V: {matched_counts['V']}",
        f"- Kandydaci E: {matched_counts['E']}",
        f"- Kandydaci R: {matched_counts['R']}",
    ]
    _write_report(report_path, lines)

    return SummaryResult(
        marker_counts=marker_counts,
        source_counts=snapshot.source_counts,
        matched_counts=matched_counts,
        latest_csv_file=snapshot.latest_csv_file,
        report_file=str(report_path),
    )


def clear_markers(
    *,
    main_config: FirebirdConnConfig,
    commit: bool,
) -> ActionResult:
    """Usuwa markery /V /E /R oraz formy legacy (VM/EMAIL/REMOTE) z EWIDENCJA."""
    rows_before = load_main_devices(main_config)
    marker_before = _marker_counts(rows_before)
    updates = _prepare_clear_updates(rows_before)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = _timestamp_utc()
    map_path = REPORT_DIR / f"raport_kp_clear_map_{stamp}.csv"
    rollback_path = REPORT_DIR / f"raport_kp_clear_rollback_{stamp}.sql"
    report_path = REPORT_DIR / f"raport_kp_clear_{stamp}.md"

    _write_map_file(map_path, updates)
    _write_rollback_file(
        rollback_path,
        updates,
        title="Rollback czyszczenia markerów KP (/V /E /R + legacy)",
    )

    updated, skipped, errors = _apply_updates(main_config, updates, commit=commit)
    rows_after = load_main_devices(main_config)
    marker_after = _marker_counts(rows_after)

    lines = [
        "# Raport czyszczenia markerów KP",
        "",
        f"Czas UTC: {stamp}",
        "",
        "## Wejście",
        f"- Kandydaci do zmiany: {len(updates)}",
        f"- Markery przed V/E/R: {marker_before['V']} / {marker_before['E']} / {marker_before['R']}",
        "",
        "## Wynik",
        f"- Commit: {'TAK' if commit else 'NIE (dry-run)'}",
        f"- Zaktualizowane: {updated}",
        f"- Pominiete: {skipped}",
        f"- Błędy: {errors}",
        f"- Markery po V/E/R: {marker_after['V']} / {marker_after['E']} / {marker_after['R']}",
        "",
        "## Artefakty",
        f"- Mapowanie: `{map_path}`",
        f"- Rollback: `{rollback_path}`",
    ]
    _write_report(report_path, lines)

    return ActionResult(
        success=True,
        message="Czyszczenie markerów zakończone.",
        commit=commit,
        candidates=len(updates),
        updated=updated,
        skipped=skipped,
        errors=errors,
        marker_counts_before=marker_before,
        marker_counts_after=marker_after,
        source_counts=None,
        report_file=str(report_path),
        map_file=str(map_path),
        rollback_file=str(rollback_path),
    )


def rebuild_markers(
    *,
    main_config: FirebirdConnConfig,
    v_config: FirebirdConnConfig,
    source_config: KpRepairSourceConfig,
    commit: bool,
    email_lookback_months: int | None = None,
) -> ActionResult:
    """Czyści i odtwarza markery V/E/R na podstawie skonfigurowanych źródeł."""
    rows_before = load_main_devices(main_config)
    marker_before = _marker_counts(rows_before)
    snapshot = build_source_snapshot(
        main_config=main_config,
        v_config=v_config,
        source_config=source_config,
        email_lookback_months=email_lookback_months,
    )
    updates = _prepare_rebuild_updates(rows_before, snapshot)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = _timestamp_utc()
    map_path = REPORT_DIR / f"raport_kp_rebuild_map_{stamp}.csv"
    rollback_path = REPORT_DIR / f"raport_kp_rebuild_rollback_{stamp}.sql"
    report_path = REPORT_DIR / f"raport_kp_rebuild_{stamp}.md"
    _write_map_file(map_path, updates)
    _write_rollback_file(
        rollback_path,
        updates,
        title="Rollback retagowania KP (/V /E /R)",
    )

    updated, skipped, errors = _apply_updates(main_config, updates, commit=commit)
    rows_after = load_main_devices(main_config)
    marker_after = _marker_counts(rows_after)

    lines = [
        "# Raport retagowania KP",
        "",
        f"Czas UTC: {stamp}",
        "",
        "## Źródła",
        f"- V seriale: {snapshot.source_counts['V_SERIALS']}",
        f"- CSV seriale: {snapshot.source_counts['CSV_SERIALS']} (wiersze: {snapshot.source_counts['CSV_ROWS']})",
        f"- EMAIL ID_DEVICE: {snapshot.source_counts['EMAIL_DEVICE_IDS']}",
        f"- EMAIL SERIAL: {snapshot.source_counts['EMAIL_SERIALS']}",
        f"- EMAIL lookback [miesiące]: {snapshot.source_counts['EMAIL_LOOKBACK_MONTHS']}",
        f"- Najnowszy CSV: {snapshot.latest_csv_file or 'brak'}",
        "",
        "## Wynik",
        f"- Kandydaci: {len(updates)}",
        f"- Commit: {'TAK' if commit else 'NIE (dry-run)'}",
        f"- Zaktualizowane: {updated}",
        f"- Pominiete: {skipped}",
        f"- Błędy: {errors}",
        f"- Markery przed V/E/R: {marker_before['V']} / {marker_before['E']} / {marker_before['R']}",
        f"- Markery po V/E/R: {marker_after['V']} / {marker_after['E']} / {marker_after['R']}",
        "",
        "## Artefakty",
        f"- Mapowanie: `{map_path}`",
        f"- Rollback: `{rollback_path}`",
    ]
    _write_report(report_path, lines)

    return ActionResult(
        success=True,
        message="Retagowanie markerów zakończone.",
        commit=commit,
        candidates=len(updates),
        updated=updated,
        skipped=skipped,
        errors=errors,
        marker_counts_before=marker_before,
        marker_counts_after=marker_after,
        source_counts=snapshot.source_counts,
        report_file=str(report_path),
        map_file=str(map_path),
        rollback_file=str(rollback_path),
    )


__all__ = [
    "ActionResult",
    "CsvSourceTestResult",
    "FirebirdConnConfig",
    "KpRepairSourceConfig",
    "SummaryResult",
    "build_summary",
    "clear_markers",
    "rebuild_markers",
    "test_csv_source",
]
