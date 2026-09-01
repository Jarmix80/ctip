#!/usr/bin/env python3
"""Naprawia tekstową stawkę VAT jednej faktury utworzonej przez Shipping.

Skrypt jest przeznaczony do kontrolowanej korekty faktury odrzuconej przez
KSeF z powodu niekanonicznej wartości `STAWKA_VAT`. Domyślnie wykonuje tylko
odczyt i walidację. Zapis wymaga flagi `--apply`, dokładnej frazy
potwierdzającej oraz aktywnego zezwolenia na zapis do Firebirda.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

DEFAULT_REPORT_DIR = REPOSITORY_ROOT / "runtime" / "repairs"


class RepairValidationError(RuntimeError):
    """Oznacza niespełnienie warunków bezpiecznej korekty faktury."""


@dataclass(frozen=True, slots=True)
class RepairExpectation:
    """Przechowuje jawne warunki korekty wskazanego dokumentu."""

    invoice_number: str
    invoice_id: int
    wz_id: int
    expected_lines: int
    expected_vat_id: int
    source_rate: str
    target_rate: str


@dataclass(frozen=True, slots=True)
class VatLine:
    """Opisuje pola podatkowe jednej pozycji dokumentu MS."""

    row_id: int
    warehouse_item_id: int
    rate_text: str
    vat_id: int
    net_value: Decimal
    vat_value: Decimal


@dataclass(frozen=True, slots=True)
class KsefAttempt:
    """Opisuje bezpieczny podzbiór danych próby wysyłki KSeF."""

    status_code: str
    ksef_number: str | None
    reference_number: str | None


@dataclass(frozen=True, slots=True)
class RepairSnapshot:
    """Zawiera komplet danych potrzebnych do walidacji korekty."""

    invoice_id: int
    invoice_number: str
    document_kind: str
    wz_id: int
    total_net: Decimal
    total_vat: Decimal
    total_gross: Decimal
    invoice_lines: tuple[VatLine, ...]
    wz_lines: tuple[VatLine, ...]
    warehouse_rates: tuple[tuple[int, str, int], ...]
    ksef_attempts: tuple[KsefAttempt, ...]


def parse_args() -> argparse.Namespace:
    """Parsuje parametry wymagane do jednoznacznej korekty dokumentu."""
    parser = argparse.ArgumentParser(
        description=(
            "Weryfikuje i opcjonalnie poprawia STAWKA_VAT w jednej FV Shipping "
            "oraz powiązanym WZ. Bez --apply wykonuje wyłącznie dry-run."
        )
    )
    parser.add_argument("--invoice-number", required=True, help="Pełny numer faktury.")
    parser.add_argument("--expected-invoice-id", required=True, type=int)
    parser.add_argument("--expected-wz-id", required=True, type=int)
    parser.add_argument("--expected-lines", required=True, type=int)
    parser.add_argument("--expected-vat-id", default=1, type=int)
    parser.add_argument("--from-rate", default="23.0 %")
    parser.add_argument("--to-rate", default="23 %")
    parser.add_argument(
        "--confirmation",
        default="",
        help="Dla --apply: dokładnie `NAPRAW VAT <numer faktury>`. ",
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    return parser.parse_args()


def _text(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _vat_percent(rate_text: str) -> Decimal:
    normalized = rate_text.replace("%", "").replace(",", ".").strip()
    try:
        return Decimal(normalized)
    except Exception as exc:
        raise RepairValidationError(f"Nie można odczytać stawki VAT: {rate_text!r}.") from exc


def _load_lines(
    cursor: Any, *, table: str, parent_column: str, parent_id: int
) -> tuple[VatLine, ...]:
    id_column = "ID_FPOZYCJA_TABLE" if table == "FPOZYCJA" else "ID_ZAKPOZYCJA_TABLE"
    warehouse_column = "ID_MAGPOZ" if table == "FPOZYCJA" else "ID_MAGAZYN"
    cursor.execute(
        f"""
        SELECT {id_column}, {warehouse_column}, STAWKA_VAT, IDVAT, WARTOSC_NETTO, VAT
        FROM {table}
        WHERE {parent_column} = ?
        ORDER BY {id_column}
        """,
        (int(parent_id),),
    )
    return tuple(
        VatLine(
            row_id=int(row[0]),
            warehouse_item_id=int(row[1]),
            rate_text=_text(row[2]) or "",
            vat_id=int(row[3] or 0),
            net_value=Decimal(str(row[4] or 0)),
            vat_value=Decimal(str(row[5] or 0)),
        )
        for row in cursor.fetchall()
    )


def load_snapshot(connection: Any, invoice_number: str) -> RepairSnapshot:
    """Odczytuje fakturę, WZ, kartoteki i historię KSeF bez wykonywania zapisu."""
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT ID_FAKTURA_TABLE, NUMER, RODZAJ_DOK, ID_WZ,
                   SUMA_NETTO, SUMA_VAT, SUMA_BRUTTO
            FROM FAKTURA
            WHERE NUMER = ?
            """,
            (invoice_number,),
        )
        header = cursor.fetchone()
        if header is None:
            raise RepairValidationError(f"Nie znaleziono faktury {invoice_number}.")
        invoice_id = int(header[0])
        wz_id = int(header[3] or 0)

        cursor.execute(
            """
            SELECT STATUSCODE, KSEFNUMBER, REFERENCENUMBER
            FROM KSEF_FAKTURA
            WHERE ID_FAKTURA = ?
            ORDER BY ID_KF
            """,
            (invoice_id,),
        )
        ksef_attempts = tuple(
            KsefAttempt(
                status_code=_text(row[0]) or "",
                ksef_number=_text(row[1]),
                reference_number=_text(row[2]),
            )
            for row in cursor.fetchall()
        )
        invoice_lines = _load_lines(
            cursor,
            table="FPOZYCJA",
            parent_column="ID_FAKTURA",
            parent_id=invoice_id,
        )
        wz_lines = _load_lines(
            cursor,
            table="ZAKPOZYCJA",
            parent_column="ID_ZAKUPY",
            parent_id=wz_id,
        )
        warehouse_item_ids = sorted(
            {line.warehouse_item_id for line in (*invoice_lines, *wz_lines)}
        )
        warehouse_rates: tuple[tuple[int, str, int], ...] = ()
        if warehouse_item_ids:
            placeholders = ",".join("?" for _ in warehouse_item_ids)
            cursor.execute(
                f"""
                SELECT ID_MAGAZYN_TABLE, VAT_STAWKA, IDVAT
                FROM MAGAZYN
                WHERE ID_MAGAZYN_TABLE IN ({placeholders})
                ORDER BY ID_MAGAZYN_TABLE
                """,
                tuple(warehouse_item_ids),
            )
            warehouse_rates = tuple(
                (int(row[0]), _text(row[1]) or "", int(row[2] or 0)) for row in cursor.fetchall()
            )
        return RepairSnapshot(
            invoice_id=invoice_id,
            invoice_number=_text(header[1]) or "",
            document_kind=_text(header[2]) or "",
            wz_id=wz_id,
            total_net=Decimal(str(header[4] or 0)),
            total_vat=Decimal(str(header[5] or 0)),
            total_gross=Decimal(str(header[6] or 0)),
            invoice_lines=invoice_lines,
            wz_lines=wz_lines,
            warehouse_rates=warehouse_rates,
            ksef_attempts=ksef_attempts,
        )
    finally:
        cursor.close()


def validate_snapshot(snapshot: RepairSnapshot, expected: RepairExpectation) -> str:
    """Sprawdza wszystkie warunki korekty i zwraca stan dokumentu."""
    if snapshot.invoice_number != expected.invoice_number:
        raise RepairValidationError("Numer odczytanej faktury nie zgadza się z parametrem.")
    if snapshot.invoice_id != expected.invoice_id:
        raise RepairValidationError("ID faktury nie zgadza się z wartością oczekiwaną.")
    if snapshot.wz_id != expected.wz_id:
        raise RepairValidationError("ID powiązanego WZ nie zgadza się z wartością oczekiwaną.")
    if snapshot.document_kind != "KPSK":
        raise RepairValidationError("Dokument nie jest fakturą KPSK.")
    if len(snapshot.invoice_lines) != expected.expected_lines:
        raise RepairValidationError("Liczba pozycji faktury nie zgadza się z oczekiwaną.")
    if len(snapshot.wz_lines) != expected.expected_lines:
        raise RepairValidationError("Liczba pozycji WZ nie zgadza się z oczekiwaną.")
    if not snapshot.ksef_attempts:
        raise RepairValidationError("Faktura nie ma zarejestrowanej próby wysyłki KSeF.")
    if any(attempt.ksef_number for attempt in snapshot.ksef_attempts):
        raise RepairValidationError(
            "Faktura ma już numer KSeF; automatyczna korekta jest zabroniona."
        )
    if snapshot.ksef_attempts[-1].status_code != "450":
        raise RepairValidationError("Ostatnia próba KSeF nie ma oczekiwanego statusu 450.")

    lines = (*snapshot.invoice_lines, *snapshot.wz_lines)
    if any(line.vat_id != expected.expected_vat_id for line in lines):
        raise RepairValidationError("Pozycje mają inny identyfikator VAT niż oczekiwany.")
    source_count = sum(line.rate_text == expected.source_rate for line in lines)
    target_count = sum(line.rate_text == expected.target_rate for line in lines)
    if target_count == len(lines):
        state = "already_corrected"
    elif source_count == len(lines):
        state = "ready"
    else:
        raise RepairValidationError("Pozycje zawierają mieszane albo nieoczekiwane stawki VAT.")

    expected_rate = _vat_percent(expected.target_rate)
    for line in lines:
        expected_vat = _money(line.net_value * expected_rate / Decimal("100"))
        if _money(line.vat_value) != expected_vat:
            raise RepairValidationError(
                f"Kwota VAT pozycji {line.row_id} nie odpowiada stawce docelowej."
            )
    warehouse_rates = {
        item_id: (rate_text, vat_id) for item_id, rate_text, vat_id in snapshot.warehouse_rates
    }
    expected_items = {line.warehouse_item_id for line in lines}
    if set(warehouse_rates) != expected_items:
        raise RepairValidationError("Nie odczytano wszystkich kartotek magazynowych pozycji.")
    if any(
        rate_text != expected.target_rate or vat_id != expected.expected_vat_id
        for rate_text, vat_id in warehouse_rates.values()
    ):
        raise RepairValidationError("Kartoteka magazynowa nie potwierdza docelowej stawki VAT.")
    return state


def apply_repair(connection: Any, expected: RepairExpectation) -> RepairSnapshot:
    """Wykonuje dwie zawężone aktualizacje i sprawdza wynik przed commitem."""
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            UPDATE FPOZYCJA
            SET STAWKA_VAT = ?
            WHERE ID_FAKTURA = ? AND STAWKA_VAT = ? AND IDVAT = ?
            """,
            (
                expected.target_rate,
                expected.invoice_id,
                expected.source_rate,
                expected.expected_vat_id,
            ),
        )
        cursor.execute(
            """
            UPDATE ZAKPOZYCJA
            SET STAWKA_VAT = ?
            WHERE ID_ZAKUPY = ? AND STAWKA_VAT = ? AND IDVAT = ?
            """,
            (
                expected.target_rate,
                expected.wz_id,
                expected.source_rate,
                expected.expected_vat_id,
            ),
        )
    finally:
        cursor.close()
    corrected = load_snapshot(connection, expected.invoice_number)
    if validate_snapshot(corrected, expected) != "already_corrected":
        raise RepairValidationError("Kontrola po aktualizacji nie potwierdziła poprawnej stawki.")
    return corrected


def _snapshot_report(snapshot: RepairSnapshot, *, mode: str, status: str) -> dict[str, Any]:
    return {
        "mode": mode,
        "status": status,
        "invoice_id": snapshot.invoice_id,
        "invoice_number": snapshot.invoice_number,
        "wz_id": snapshot.wz_id,
        "invoice_lines": len(snapshot.invoice_lines),
        "wz_lines": len(snapshot.wz_lines),
        "invoice_rates": sorted({line.rate_text for line in snapshot.invoice_lines}),
        "wz_rates": sorted({line.rate_text for line in snapshot.wz_lines}),
        "ksef_attempts": [asdict(attempt) for attempt in snapshot.ksef_attempts],
        "totals": {
            "net": str(snapshot.total_net),
            "vat": str(snapshot.total_vat),
            "gross": str(snapshot.total_gross),
        },
    }


def write_report(report_dir: Path, payload: dict[str, Any]) -> Path:
    """Zapisuje bezpieczny raport operacji w ignorowanym katalogu runtime."""
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = report_dir / f"shipping_vat_repair_{stamp}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def run() -> int:
    """Wykonuje dry-run albo kontrolowaną korektę dokumentu."""
    from app.services.firebird_runtime import firebird_connection, firebird_writes_enabled

    args = parse_args()
    expected = RepairExpectation(
        invoice_number=args.invoice_number.strip(),
        invoice_id=args.expected_invoice_id,
        wz_id=args.expected_wz_id,
        expected_lines=args.expected_lines,
        expected_vat_id=args.expected_vat_id,
        source_rate=args.from_rate.strip(),
        target_rate=args.to_rate.strip(),
    )
    required_confirmation = f"NAPRAW VAT {expected.invoice_number}"
    if args.apply and args.confirmation != required_confirmation:
        raise SystemExit(f"Niepoprawna fraza potwierdzająca. Wymagana: {required_confirmation}")
    if args.apply:
        enabled, reason = firebird_writes_enabled()
        if not enabled:
            raise SystemExit(reason or "Zapis do Firebirda jest zablokowany.")

    connection = firebird_connection()
    try:
        before = load_snapshot(connection, expected.invoice_number)
        state = validate_snapshot(before, expected)
        if not args.apply:
            connection.rollback()
            payload = _snapshot_report(before, mode="dry-run", status=state)
        elif state == "already_corrected":
            connection.rollback()
            payload = _snapshot_report(before, mode="apply", status=state)
        else:
            corrected = apply_repair(connection, expected)
            connection.commit()
            payload = _snapshot_report(corrected, mode="apply", status="corrected")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    report_path = write_report(args.report_dir, payload)
    payload["report_path"] = str(report_path)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
