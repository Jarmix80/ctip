#!/usr/bin/env python3
"""Automatyczna ekstrakcja NIP i numeru umowy z PDF-ow w katalogu inbox."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from watchfiles import watch

from app.services.contract_pdf_parser import parse_contract_pdf


def parse_args() -> argparse.Namespace:
    """Buduje i parsuje argumenty CLI."""
    parser = argparse.ArgumentParser(
        description=(
            "Przetwarza pliki PDF z inbox i zapisuje wynik do plikow " "*.pdf.parsed.json."
        )
    )
    parser.add_argument(
        "--inbox-dir",
        default="inbox",
        help="Katalog z plikami PDF (domyslnie: inbox).",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Nasluchuje zmian i przetwarza nowe/zmienione PDF-y w czasie rzeczywistym.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Wymusza ponowne przetworzenie nawet gdy istnieje aktualny plik wynikowy.",
    )
    return parser.parse_args()


def result_path_for(pdf_path: Path) -> Path:
    """Zwraca sciezke pliku JSON z wynikiem ekstrakcji."""
    return pdf_path.with_suffix(f"{pdf_path.suffix}.parsed.json")


def should_process(pdf_path: Path, force: bool) -> bool:
    """Decyduje, czy dany PDF wymaga przetworzenia."""
    if force:
        return True
    result_path = result_path_for(pdf_path)
    if not result_path.exists():
        return True
    return result_path.stat().st_mtime < pdf_path.stat().st_mtime


def process_pdf(pdf_path: Path, force: bool) -> bool:
    """Przetwarza pojedynczy PDF i zapisuje wynik do JSON."""
    if pdf_path.suffix.lower() != ".pdf":
        return False
    if not pdf_path.exists() or not pdf_path.is_file():
        return False
    if not should_process(pdf_path, force=force):
        return False

    data = parse_contract_pdf(pdf_path)

    notes: list[str] = []
    if not data.nip:
        notes.append("Nie wykryto wiarygodnego NIP kontrahenta.")
    if not data.contract_number:
        notes.append("Nie wykryto numeru umowy.")

    payload = {
        "source_file": str(pdf_path),
        "processed_at": datetime.now(UTC).isoformat(),
        "nip": data.nip,
        "contract_number": data.contract_number,
        "nips_found": list(data.nips_found),
        "contract_number_candidates": list(data.contract_candidates),
        "notes": notes,
    }

    result_path = result_path_for(pdf_path)
    result_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] {pdf_path} -> {result_path}")
    if notes:
        print(f"[INFO] Ostrzezenia: {'; '.join(notes)}")
    return True


def process_existing(inbox_dir: Path, force: bool) -> int:
    """Przetwarza wszystkie PDF-y z katalogu inbox (rekurencyjnie)."""
    processed = 0
    for pdf_path in sorted(inbox_dir.rglob("*.pdf")):
        if process_pdf(pdf_path, force=force):
            processed += 1
    return processed


def watch_loop(inbox_dir: Path, force: bool) -> None:
    """Uruchamia petle nasluchu zmian w katalogu inbox."""
    print(f"[INFO] Nasluch katalogu: {inbox_dir}")
    for changes in watch(inbox_dir, recursive=True):
        for _, changed_path in changes:
            path = Path(changed_path)
            if path.suffix.lower() != ".pdf":
                continue
            process_pdf(path, force=force)


def main() -> int:
    """Punkt startowy CLI."""
    args = parse_args()
    inbox_dir = Path(args.inbox_dir).resolve()

    if not inbox_dir.exists() or not inbox_dir.is_dir():
        print(f"[ERR] Katalog nie istnieje: {inbox_dir}")
        return 1

    processed = process_existing(inbox_dir, force=args.force)
    print(f"[INFO] Przetworzono plikow: {processed}")

    if args.watch:
        watch_loop(inbox_dir, force=args.force)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
