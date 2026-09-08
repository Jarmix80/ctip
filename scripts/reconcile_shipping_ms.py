#!/usr/bin/env python3
"""Audytuje i opcjonalnie uzgadnia aktywne przesyłki Shipping ze stanem MS."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

CONFIRMATION = "UZGODNIJ SHIPPING Z MS"
DEFAULT_REPORT_DIR = REPOSITORY_ROOT / "runtime" / "repairs"


def parse_args() -> argparse.Namespace:
    """Parsuje tryb dry-run albo jawnie potwierdzony zapis w CTIP."""
    parser = argparse.ArgumentParser(
        description="Porównuje aktywne przesyłki Shipping z dokumentami Menadżera Serwisu."
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--confirmation",
        default="",
        help=f"Dla --apply wymagane jest dokładnie: {CONFIRMATION}",
    )
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    return parser.parse_args()


async def run(args: argparse.Namespace) -> dict:
    """Uruchamia wspólną usługę uzgadniania i zwraca raport operacyjny."""
    from app.core.config import settings
    from app.services.shipping_ms_reconciliation import synchronize_shipping_ms

    if args.apply and args.confirmation != CONFIRMATION:
        raise ValueError(f"Niepoprawne potwierdzenie. Wymagana fraza: {CONFIRMATION}")
    if args.apply and not settings.shipping_ms_reconcile_enabled:
        raise ValueError(
            "Zapis jest zablokowany. Ustaw SHIPPING_MS_RECONCILE_ENABLED=true w aktywnym środowisku."
        )
    result = await synchronize_shipping_ms(trigger_type="script", apply=bool(args.apply))
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "apply" if args.apply else "dry-run",
        **result,
    }


def main() -> int:
    """Konfiguruje Windows, zapisuje raport JSON i ustala kod zakończenia."""
    args = parse_args()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        report = asyncio.run(run(args))
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1
    args.report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    path = args.report_dir / f"shipping_ms_reconciliation_{report['mode']}_{stamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**report, "report_path": str(path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
