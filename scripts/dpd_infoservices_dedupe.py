#!/usr/bin/env python3
"""Bezpiecznie grupuje historyczne aliasy zdarzeń DPD InfoServices.

Bez parametrów skrypt wykonuje wyłącznie dry-run. Zapis wymaga tokenu stanu
oraz dokładnej frazy potwierdzającej. Operacja nie usuwa rekordów technicznych.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

DEFAULT_REPORT_DIR = REPOSITORY_ROOT / "runtime" / "shipping_dpd_dedupe"
APPLY_CONFIRMATION = "ZASTOSUJ DEDUPLIKACJE DPD"


def parse_args() -> argparse.Namespace:
    """Parsuje tryb pracy i zabezpieczenia operacji modyfikującej."""
    parser = argparse.ArgumentParser(
        description=(
            "Grupuje techniczne kopie tych samych zdarzeń DPD. "
            "Bez --apply i --rollback wykonuje wyłącznie dry-run."
        )
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--apply", action="store_true")
    modes.add_argument("--rollback", metavar="RUN_ID")
    parser.add_argument("--state-token", default="")
    parser.add_argument("--confirmation", default="")
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    return parser.parse_args()


def _report_path(report_dir: Path, payload: dict[str, Any]) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    if payload.get("run_id"):
        suffix = str(payload["run_id"])
    else:
        suffix = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return report_dir / f"dpd_infoservices_dedupe_{payload['mode']}_{suffix}.json"


def _load_apply_report(report_dir: Path, run_id: str) -> dict[str, Any]:
    normalized_run_id = str(UUID(str(run_id)))
    path = report_dir / f"dpd_infoservices_dedupe_apply_{normalized_run_id}.json"
    if not path.is_file():
        raise ValueError(f"Brak raportu przebiegu wymaganego do rollbacku: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("mode") != "apply" or payload.get("run_id") != normalized_run_id:
        raise ValueError("Raport rollbacku ma niezgodną tożsamość przebiegu.")
    return payload


async def execute(args: argparse.Namespace) -> dict[str, Any]:
    """Wykonuje podgląd, zapis albo rollback w jednej transakcji PostgreSQL."""
    from app.db.session import AsyncSessionLocal
    from app.services.dpd_tracking_dedupe import (
        apply_dpd_tracking_dedupe,
        preview_dpd_tracking_dedupe,
        rollback_dpd_tracking_dedupe,
    )

    async with AsyncSessionLocal() as session:
        try:
            if args.rollback:
                normalized_run_id = str(UUID(str(args.rollback)))
                required = f"WYCOFAJ DEDUPLIKACJE DPD {normalized_run_id}"
                if args.confirmation != required:
                    raise ValueError(f"Niepoprawne potwierdzenie. Wymagana fraza: {required}")
                report = _load_apply_report(args.report_dir, normalized_run_id)
                result = await rollback_dpd_tracking_dedupe(
                    session,
                    run_id=normalized_run_id,
                    rollback_state=report.get("rollback_state") or [],
                )
                await session.commit()
                return result
            if args.apply:
                if args.confirmation != APPLY_CONFIRMATION:
                    raise ValueError(
                        f"Niepoprawne potwierdzenie. Wymagana fraza: {APPLY_CONFIRMATION}"
                    )
                if not args.state_token:
                    raise ValueError("Tryb --apply wymaga tokenu z aktualnego dry-run.")
                result = await apply_dpd_tracking_dedupe(
                    session,
                    expected_state_token=args.state_token,
                )
                await session.commit()
                return result
            result = await preview_dpd_tracking_dedupe(session)
            await session.rollback()
            return result
        except Exception:
            await session.rollback()
            raise


def run() -> int:
    """Uruchamia operację i zapisuje raport poza repozytorium wersjonowanym."""
    from app.core.asyncio_compat import configure_asyncio_for_windows
    from app.services.dpd_tracking_dedupe import DpdTrackingDedupeValidationError

    args = parse_args()
    configure_asyncio_for_windows()
    try:
        payload = asyncio.run(execute(args))
    except (DpdTrackingDedupeValidationError, RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    path = _report_path(args.report_dir, payload)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    public_payload = {key: value for key, value in payload.items() if key != "rollback_state"}
    public_payload["report_path"] = str(path)
    print(json.dumps(public_payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
