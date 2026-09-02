#!/usr/bin/env python3
"""Uruchamia kontrolowany pilot kamieni milowych dla jednej archiwalnej przesyłki.

Domyślnie skrypt wyłącznie odczytuje PostgreSQL, zdarzenia DPD i pola zlecenia
Menadżera Serwisu. Zapis wymaga tokenu zwróconego przez dry-run oraz dokładnej
frazy potwierdzającej. Globalna synchronizacja pól Firebirda musi pozostać
wyłączona przez cały pilot.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

DEFAULT_REPORT_DIR = REPOSITORY_ROOT / "runtime" / "shipping_pilots"
ORDER_PATTERN = re.compile(r"^(?P<order_id>\d{1,9})/(?P<order_year>\d{4})$")
WAYBILL_PATTERN = re.compile(r"^[A-Za-z0-9-]{8,32}$")


def parse_args() -> argparse.Namespace:
    """Parsuje jednoznaczny cel, tryb operacji i zabezpieczenia zapisu."""
    parser = argparse.ArgumentParser(
        description=(
            "Pokazuje albo zapisuje kamienie milowe DPD dla jednego archiwalnego "
            "zlecenia. Bez --apply i --rollback wykonuje wyłącznie dry-run."
        )
    )
    parser.add_argument("--order", required=True, help="Numer zlecenia, np. 18517/2026.")
    parser.add_argument("--waybill", required=True, help="Dokładny numer listu DPD.")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--apply", action="store_true", help="Wykonaj zatwierdzony dry-run.")
    modes.add_argument(
        "--rollback",
        metavar="PILOT_RUN_ID",
        help="Wycofaj dokładnie jeden wcześniej wykonany przebieg pilota.",
    )
    parser.add_argument(
        "--state-token",
        default="",
        help="Token stanu zwrócony przez bezpośrednio poprzedzający dry-run.",
    )
    parser.add_argument(
        "--confirmation",
        default="",
        help="Dokładna fraza potwierdzająca operację zapisu.",
    )
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    return parser.parse_args()


def parse_order_number(value: str) -> tuple[int, int, str]:
    """Waliduje numer zlecenia i zwraca jego kanoniczną reprezentację."""
    normalized = str(value or "").strip()
    match = ORDER_PATTERN.fullmatch(normalized)
    if match is None:
        raise ValueError("Numer zlecenia musi mieć format NNNNN/RRRR.")
    order_id = int(match.group("order_id"))
    order_year = int(match.group("order_year"))
    return order_id, order_year, f"{order_id}/{order_year}"


def parse_waybill(value: str) -> str:
    """Waliduje bezpieczny format numeru listu przekazywanego do zapytań."""
    normalized = str(value or "").strip()
    if WAYBILL_PATTERN.fullmatch(normalized) is None:
        raise ValueError("Numer listu zawiera niedozwolone znaki albo ma błędną długość.")
    return normalized


def write_report(report_dir: Path, payload: dict[str, Any]) -> Path:
    """Zapisuje raport w ignorowanym katalogu runtime bez danych uwierzytelniających."""
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    mode = str(payload.get("mode") or "unknown").replace("-", "_")
    path = report_dir / f"shipping_dpd_milestone_pilot_{mode}_{stamp}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


async def execute(args: argparse.Namespace) -> dict[str, Any]:
    """Wykonuje podgląd, zapis albo rollback w jednej sesji PostgreSQL."""
    from app.db.session import AsyncSessionLocal
    from app.services.shipping_milestones import (
        apply_archived_shipping_milestone_pilot,
        preview_archived_shipping_milestone_pilot,
        rollback_archived_shipping_milestone_pilot,
    )

    order_id, order_year, order_number = parse_order_number(args.order)
    waybill = parse_waybill(args.waybill)
    async with AsyncSessionLocal() as session:
        try:
            if args.rollback:
                required_confirmation = f"WYCOFAJ PILOT DPD {args.rollback}"
                if args.confirmation != required_confirmation:
                    raise ValueError(
                        "Niepoprawna fraza potwierdzająca. " f"Wymagana: {required_confirmation}"
                    )
                result = await rollback_archived_shipping_milestone_pilot(
                    session,
                    order_id=order_id,
                    order_year=order_year,
                    waybill=waybill,
                    pilot_run_id=str(args.rollback).strip(),
                )
                await session.commit()
                return result
            if args.apply:
                required_confirmation = f"URUCHOM PILOT DPD {order_number} {waybill}"
                if args.confirmation != required_confirmation:
                    raise ValueError(
                        "Niepoprawna fraza potwierdzająca. " f"Wymagana: {required_confirmation}"
                    )
                if not str(args.state_token).strip():
                    raise ValueError("Tryb --apply wymaga tokenu z aktualnego dry-run.")
                result = await apply_archived_shipping_milestone_pilot(
                    session,
                    order_id=order_id,
                    order_year=order_year,
                    waybill=waybill,
                    expected_state_token=str(args.state_token).strip(),
                )
                await session.commit()
                return result

            result = await preview_archived_shipping_milestone_pilot(
                session,
                order_id=order_id,
                order_year=order_year,
                waybill=waybill,
            )
            await session.rollback()
            result["required_confirmation"] = f"URUCHOM PILOT DPD {order_number} {waybill}"
            return result
        except Exception:
            await session.rollback()
            raise


def run() -> int:
    """Uruchamia narzędzie i zapisuje raport niezależnie od wybranego trybu."""
    from app.core.asyncio_compat import configure_asyncio_for_windows
    from app.services.shipping_firebird import ShippingOrderStateConflict
    from app.services.shipping_milestones import ShippingMilestonePilotValidationError

    args = parse_args()
    configure_asyncio_for_windows()
    try:
        payload = asyncio.run(execute(args))
    except (
        ShippingMilestonePilotValidationError,
        ShippingOrderStateConflict,
        RuntimeError,
        ValueError,
    ) as exc:
        raise SystemExit(str(exc)) from exc
    report_path = write_report(args.report_dir, payload)
    payload["report_path"] = str(report_path)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
