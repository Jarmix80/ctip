#!/usr/bin/env python3
"""Kontrolowany backfill historii InfoServices dla listów utworzonych w CTIP."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import distinct, select

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))


def parse_args() -> argparse.Namespace:
    """Parsuje bezpieczne parametry podglądu i wykonania backfillu."""
    parser = argparse.ArgumentParser(
        description=(
            "Pobiera pełną historię DPD InfoServices dla produkcyjnych listów zapisanych w CTIP. "
            "Bez --apply wyświetla wyłącznie plan operacji."
        )
    )
    parser.add_argument(
        "--apply", action="store_true", help="Wykonaj zapytania i zapisz zdarzenia."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="Maksymalna liczba listów w jednym uruchomieniu (domyślnie 500).",
    )
    parser.add_argument(
        "--waybill",
        action="append",
        default=[],
        help="Ogranicz operację do wskazanego numeru listu; parametr można powtórzyć.",
    )
    return parser.parse_args()


async def _known_waybills(limit: int, selected: list[str]) -> list[str]:
    """Pobiera produkcyjne numery listów zapisane w przesyłkach CTIP."""
    from app.db.session import AsyncSessionLocal
    from app.models import ShippingShipment

    normalized = list(dict.fromkeys(value.strip() for value in selected if value.strip()))
    async with AsyncSessionLocal() as session:
        statement = (
            select(distinct(ShippingShipment.tracking_number))
            .where(
                ShippingShipment.provider == "dpd",
                ShippingShipment.provider_mode == "production",
                ShippingShipment.tracking_number.is_not(None),
            )
            .order_by(ShippingShipment.tracking_number)
            .limit(max(1, min(limit, 5000)))
        )
        if normalized:
            statement = statement.where(ShippingShipment.tracking_number.in_(normalized))
        return [str(value) for value in (await session.execute(statement)).scalars() if value]


async def main() -> int:
    """Pokazuje plan albo wykonuje idempotentny backfill."""
    from app.services.dpd_infoservices_sync import backfill_dpd_waybills

    args = parse_args()
    waybills = await _known_waybills(args.limit, args.waybill)
    if not args.apply:
        print(
            json.dumps(
                {
                    "mode": "dry-run",
                    "waybill_count": len(waybills),
                    "waybills": waybills,
                    "next_step": "Uruchom ponownie z --apply po weryfikacji listy.",
                },
                ensure_ascii=False,
            )
        )
        return 0
    result = await backfill_dpd_waybills(waybills)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
