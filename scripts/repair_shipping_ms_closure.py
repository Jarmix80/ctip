#!/usr/bin/env python3
"""Uzupełnia pełne zamknięcie zleceń MS zakończonych wcześniej przez Shipping.

Skrypt odczytuje z PostgreSQL archiwalne daty i operatorów zamknięcia, a następnie
porównuje je z odpowiadającymi rekordami `ZLECENIE` w Firebirdzie. Domyślnie
wykonuje wyłącznie dry-run. Zapis wymaga flagi `--apply`, dokładnej frazy
potwierdzającej oraz aktywnego zezwolenia na zapis do Firebirda.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

DEFAULT_REPORT_DIR = REPOSITORY_ROOT / "runtime" / "repairs"
CONFIRMATION = "NAPRAW ZAMKNIECIA SHIPPING"
WARSAW = ZoneInfo("Europe/Warsaw")
CLOSURE_MARKER = re.compile(r"(?:^|,\s*)Zamknął\s*:", flags=re.IGNORECASE)


class RepairValidationError(RuntimeError):
    """Oznacza rozbieżność blokującą bezpieczną korektę zleceń."""


@dataclass(frozen=True, slots=True)
class ClosureCandidate:
    """Przechowuje dane referencyjne zamknięcia zapisane w CTIP."""

    order_table_id: int
    order_id: int
    order_year: int
    closed_date: date
    operator_name: str
    tracking_number: str
    rw_id: int | None
    rw_number: str | None
    wz_id: int | None
    wz_number: str | None
    invoice_id: int | None
    invoice_number: str | None


@dataclass(frozen=True, slots=True)
class FirebirdOrderSnapshot:
    """Opisuje pola zlecenia MS objęte kontrolą naprawy."""

    order_table_id: int
    order_id: int
    order_year: int
    state: str
    closed_date: date | None
    operator_text: str | None
    tracking_number: str | None
    rw_id: int | None
    wz_id: int | None
    invoice_id: int | None
    document_number: str | None


def configure_event_loop_policy() -> None:
    """Ustawia na Windows pętlę zgodną z asynchronicznym sterownikiem PostgreSQL."""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def parse_args() -> argparse.Namespace:
    """Parsuje tryb wykonania i parametry raportu."""
    parser = argparse.ArgumentParser(
        description=(
            "Sprawdza i opcjonalnie uzupełnia DATA_Z oraz operatora zamknięcia "
            "dla zleceń zakończonych przez Shipping."
        )
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--confirmation",
        default="",
        help=f"Dla --apply: dokładnie `{CONFIRMATION}`.",
    )
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    return parser.parse_args()


def _text(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _date_value(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else None


def _int_value(value: Any) -> int | None:
    return int(value) if value is not None else None


def _closed_operator(existing_operator: Any, operator_name: str) -> str:
    """Buduje idempotentny tekst operatora zgodny z polem `ZLECENIE.OPERATOR`."""
    existing = _text(existing_operator) or ""
    if CLOSURE_MARKER.search(existing):
        return existing[:250]
    suffix = f"Zamknął :{_text(operator_name) or 'CTIP'}"
    available = max(0, 250 - len(suffix) - (2 if existing else 0))
    prefix = existing[:available].rstrip(" ,")
    return f"{prefix}, {suffix}" if prefix else suffix[:250]


async def load_candidates() -> tuple[ClosureCandidate, ...]:
    """Odczytuje zamknięte przesyłki i ich niezmienny ślad archiwalny z PostgreSQL."""
    from app.db.session import AsyncSessionLocal, engine

    statement = text(
        """
        SELECT sc.firebird_order_table_id, sc.firebird_order_id, sc.firebird_order_year,
               ss.closed_at,
               ss.archive_snapshot->'operators'->'closed'->>'name' AS operator_name,
               ss.tracking_number,
               ss.firebird_rw_id, ss.firebird_rw_number,
               ss.firebird_wz_id, ss.firebird_wz_number,
               ss.firebird_invoice_id, ss.firebird_invoice_number
        FROM ctip.shipping_case sc
        JOIN ctip.shipping_shipment ss ON ss.shipping_case_id = sc.id
        WHERE sc.status = 'closed' AND ss.status = 'closed' AND ss.closed_at IS NOT NULL
        ORDER BY ss.closed_at, sc.firebird_order_table_id
        """
    )
    try:
        async with AsyncSessionLocal() as session:
            rows = (await session.execute(statement)).all()
    finally:
        await engine.dispose()

    candidates: list[ClosureCandidate] = []
    for row in rows:
        closed_at = row[3]
        operator_name = _text(row[4])
        tracking_number = _text(row[5])
        if not isinstance(closed_at, datetime) or not operator_name or not tracking_number:
            raise RepairValidationError(
                f"Zlecenie {row[0]} nie ma kompletnej daty, operatora lub numeru przesyłki w CTIP."
            )
        candidates.append(
            ClosureCandidate(
                order_table_id=int(row[0]),
                order_id=int(row[1]),
                order_year=int(row[2]),
                closed_date=closed_at.astimezone(WARSAW).date(),
                operator_name=operator_name,
                tracking_number=tracking_number,
                rw_id=_int_value(row[6]),
                rw_number=_text(row[7]),
                wz_id=_int_value(row[8]),
                wz_number=_text(row[9]),
                invoice_id=_int_value(row[10]),
                invoice_number=_text(row[11]),
            )
        )
    return tuple(candidates)


def load_firebird_order(
    cursor: Any, order_table_id: int, *, with_lock: bool
) -> FirebirdOrderSnapshot | None:
    """Odczytuje kontrolowane pola jednego zlecenia Firebird."""
    lock_clause = " WITH LOCK" if with_lock else ""
    cursor.execute(
        f"""
        SELECT ID_ZLECENIE_TABLE, ID_ZLECENIE, ROK, STAN, DATA_Z, OPERATOR,
               PRZESYLKA, ID_RW, ID_WZ, ID_FAKTURA, FAKTURA
        FROM ZLECENIE WHERE ID_ZLECENIE_TABLE = ?{lock_clause}
        """,
        (int(order_table_id),),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return FirebirdOrderSnapshot(
        order_table_id=int(row[0]),
        order_id=int(row[1]),
        order_year=int(row[2]),
        state=(_text(row[3]) or "").upper(),
        closed_date=_date_value(row[4]),
        operator_text=_text(row[5]),
        tracking_number=_text(row[6]),
        rw_id=_int_value(row[7]),
        wz_id=_int_value(row[8]),
        invoice_id=_int_value(row[9]),
        document_number=_text(row[10]),
    )


def _expected_document(
    candidate: ClosureCandidate,
) -> tuple[int | None, int | None, int | None, str]:
    if candidate.invoice_id is not None and candidate.invoice_number:
        return None, None, candidate.invoice_id, candidate.invoice_number
    if candidate.rw_id is not None and candidate.rw_number:
        return candidate.rw_id, None, None, candidate.rw_number
    if candidate.wz_id is not None and candidate.wz_number:
        return None, candidate.wz_id, None, candidate.wz_number
    raise RepairValidationError(
        f"Zlecenie {candidate.order_table_id} nie ma kompletnego dokumentu w archiwum CTIP."
    )


def validate_candidate(
    candidate: ClosureCandidate,
    snapshot: FirebirdOrderSnapshot,
) -> tuple[str, str]:
    """Waliduje tożsamość, dokument i wylicza docelowego operatora zamknięcia."""
    if (snapshot.order_id, snapshot.order_year) != (candidate.order_id, candidate.order_year):
        raise RepairValidationError(
            f"Zlecenie {candidate.order_table_id} ma w Firebirdzie inny numer lub rok."
        )
    if snapshot.state != "Z":
        raise RepairValidationError(
            f"Zlecenie {candidate.order_table_id} ma stan {snapshot.state or 'brak'} zamiast Z."
        )
    if snapshot.tracking_number != candidate.tracking_number:
        raise RepairValidationError(
            f"Zlecenie {candidate.order_table_id} ma inny numer przesyłki niż archiwum CTIP."
        )
    expected_document = _expected_document(candidate)
    actual_document = (
        snapshot.rw_id,
        snapshot.wz_id,
        snapshot.invoice_id,
        snapshot.document_number or "",
    )
    if actual_document != expected_document:
        raise RepairValidationError(
            f"Zlecenie {candidate.order_table_id} ma inne powiązanie dokumentu niż archiwum CTIP."
        )
    target_operator = _closed_operator(snapshot.operator_text, candidate.operator_name)
    status = (
        "już_poprawne"
        if snapshot.closed_date == candidate.closed_date
        and snapshot.operator_text == target_operator
        and CLOSURE_MARKER.search(target_operator)
        else "do_naprawy"
    )
    return status, target_operator


def repair_candidates(
    connection: Any,
    candidates: tuple[ClosureCandidate, ...],
    *,
    apply: bool,
) -> dict[str, Any]:
    """Waliduje wszystkie rekordy i opcjonalnie naprawia je w jednej transakcji."""
    cursor = connection.cursor()
    rows: list[dict[str, Any]] = []
    repaired = 0
    repair_required = 0
    already_correct = 0
    missing = 0
    try:
        for candidate in candidates:
            snapshot = load_firebird_order(cursor, candidate.order_table_id, with_lock=apply)
            if snapshot is None:
                missing += 1
                rows.append(
                    {
                        "order_table_id": candidate.order_table_id,
                        "order_number": f"{candidate.order_id}/{candidate.order_year}",
                        "status": "brak_w_firebird",
                    }
                )
                continue
            status, target_operator = validate_candidate(candidate, snapshot)
            if status == "już_poprawne":
                already_correct += 1
            else:
                repair_required += 1
                if apply:
                    cursor.execute(
                        "UPDATE ZLECENIE SET DATA_Z = ?, OPERATOR = ? WHERE ID_ZLECENIE_TABLE = ?",
                        (candidate.closed_date, target_operator, candidate.order_table_id),
                    )
                    verified = load_firebird_order(
                        cursor, candidate.order_table_id, with_lock=False
                    )
                    if verified is None:
                        raise RepairValidationError(
                            f"Zlecenie {candidate.order_table_id} zniknęło podczas weryfikacji."
                        )
                    verified_status, _ = validate_candidate(candidate, verified)
                    if verified_status != "już_poprawne":
                        raise RepairValidationError(
                            f"Zlecenie {candidate.order_table_id} nie potwierdziło pełnego zamknięcia."
                        )
                    repaired += 1
                    status = "naprawione"
            rows.append(
                {
                    "order_table_id": candidate.order_table_id,
                    "order_number": f"{candidate.order_id}/{candidate.order_year}",
                    "closed_date": candidate.closed_date.isoformat(),
                    "operator_name": candidate.operator_name,
                    "status": status,
                }
            )
    finally:
        cursor.close()
    return {
        "candidate_count": len(candidates),
        "repair_required_count": repair_required,
        "repaired_count": repaired,
        "already_correct_count": already_correct,
        "missing_count": missing,
        "rows": rows,
    }


def write_report(report_dir: Path, payload: dict[str, Any]) -> Path:
    """Zapisuje raport operacji w ignorowanym katalogu runtime."""
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = report_dir / f"shipping_ms_closure_repair_{stamp}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def run() -> int:
    """Uruchamia kontrolę albo atomową naprawę niepełnych zamknięć Shipping."""
    from app.services.firebird_runtime import firebird_connection, firebird_writes_enabled

    args = parse_args()
    if args.apply and args.confirmation != CONFIRMATION:
        raise SystemExit(f"Niepoprawna fraza potwierdzająca. Wymagana: {CONFIRMATION}")
    if args.apply:
        enabled, reason = firebird_writes_enabled()
        if not enabled:
            raise SystemExit(reason or "Zapis do Firebirda jest zablokowany.")

    configure_event_loop_policy()
    candidates = asyncio.run(load_candidates())
    connection = firebird_connection()
    try:
        report = repair_candidates(connection, candidates, apply=args.apply)
        if args.apply:
            connection.commit()
        else:
            connection.rollback()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    payload = {
        "mode": "apply" if args.apply else "dry-run",
        "generated_at": datetime.now(UTC).isoformat(),
        **report,
    }
    report_path = write_report(args.report_dir, payload)
    payload["report_path"] = str(report_path)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
