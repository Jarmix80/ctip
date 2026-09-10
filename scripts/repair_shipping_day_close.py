"""Kontrolowana naprawa zamknięcia dnia Shipping po wcześniejszym zamknięciu w MS."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from app.db.session import AsyncSessionLocal  # noqa: E402
from app.models import ShippingCase, ShippingShipment  # noqa: E402
from app.services.firebird_runtime import (  # noqa: E402
    load_firebird_runtime_config,
    use_firebird_runtime_config,
)
from app.services.shipping_firebird import load_shipping_order_state  # noqa: E402
from app.services.shipping_workflow import close_shipping_day, close_shipping_order  # noqa: E402

WARSAW = ZoneInfo("Europe/Warsaw")
APPLY_CONFIRMATION = "NAPRAW ZAMKNIECIE DNIA SHIPPING"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Naprawia jawnie wskazane zlecenie zamknięte w MS bez dokumentów, "
            "a następnie kończy pozostałe przesyłki z wybranego dnia."
        )
    )
    parser.add_argument("--business-date", type=date.fromisoformat, required=True)
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument(
        "--recover-preclosed-order-table-id",
        action="append",
        default=[],
        type=int,
        help="ID_ZLECENIE_TABLE dopuszczone do jawnej naprawy; parametr można powtarzać.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Wykonuje zapis. Bez tej flagi skrypt działa wyłącznie jako dry-run.",
    )
    parser.add_argument(
        "--confirm",
        help=f"Przy zapisie wymagana jest dokładna fraza: {APPLY_CONFIRMATION}",
    )
    return parser


async def _day_shipments(
    session: AsyncSession,
    *,
    business_date: date,
) -> list[tuple[ShippingCase, ShippingShipment]]:
    local_start = datetime.combine(business_date, time.min, tzinfo=WARSAW)
    start = local_start.astimezone(UTC)
    end = (local_start + timedelta(days=1)).astimezone(UTC)
    rows = (
        await session.execute(
            select(ShippingCase, ShippingShipment)
            .join(ShippingShipment, ShippingShipment.shipping_case_id == ShippingCase.id)
            .options(selectinload(ShippingCase.items))
            .where(
                ShippingShipment.created_at >= start,
                ShippingShipment.created_at < end,
                ShippingShipment.status.in_(("label_ready", "handed_over")),
            )
            .order_by(ShippingShipment.id)
        )
    ).all()
    return list(rows)


async def _preview(
    session: AsyncSession,
    *,
    business_date: date,
    recover_order_ids: set[int],
) -> dict[str, Any]:
    rows = await _day_shipments(session, business_date=business_date)
    items = []
    found_ids = set()
    for case, shipment in rows:
        order_table_id = int(case.firebird_order_table_id)
        state = await asyncio.to_thread(load_shipping_order_state, order_table_id)
        if order_table_id in recover_order_ids:
            found_ids.add(order_table_id)
            if not state["recoverable_preclosed"]:
                raise RuntimeError(
                    f"Zlecenie {order_table_id} nie spełnia warunków bezpiecznej naprawy."
                )
        items.append(
            {
                "order_table_id": order_table_id,
                "order_number": f"{case.firebird_order_id}/{case.firebird_order_year}",
                "invoice_required": bool(case.invoice_required),
                "shipment_id": int(shipment.id),
                "shipment_status": shipment.status,
                "ms_status": state["status"],
                "ms_status_label": state["status_label"],
                "recover_preclosed": order_table_id in recover_order_ids,
            }
        )
    missing = sorted(recover_order_ids - found_ids)
    if missing:
        raise RuntimeError(
            "W aktywnych przesyłkach dnia nie znaleziono zleceń do naprawy: "
            + ", ".join(str(value) for value in missing)
        )
    return {"shipment_count": len(items), "items": items}


async def _run(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    if args.apply and args.confirm != APPLY_CONFIRMATION:
        raise RuntimeError("Brak dokładnej frazy potwierdzającej zapis produkcyjny.")
    recover_order_ids = {
        int(value) for value in args.recover_preclosed_order_table_id if int(value) > 0
    }
    async with AsyncSessionLocal() as session:
        runtime = await load_firebird_runtime_config(session)
        with use_firebird_runtime_config(runtime):
            preview = await _preview(
                session,
                business_date=args.business_date,
                recover_order_ids=recover_order_ids,
            )
            report: dict[str, Any] = {
                "mode": "apply" if args.apply else "dry-run",
                "business_date": args.business_date.isoformat(),
                "user_id": args.user_id,
                "preview": preview,
            }
            if not args.apply:
                return report, 0

            repairs = []
            for order_table_id in sorted(recover_order_ids):
                repairs.append(
                    await close_shipping_order(
                        session,
                        order_table_id=order_table_id,
                        user_id=args.user_id,
                        allow_preclosed_without_documents=True,
                    )
                )
            report["preclosed_repairs"] = repairs
            report["day_close"] = await close_shipping_day(
                session,
                business_date=args.business_date,
                user_id=args.user_id,
            )
            remaining = await _day_shipments(session, business_date=args.business_date)
            report["remaining_active_order_table_ids"] = [
                int(case.firebird_order_table_id) for case, _shipment in remaining
            ]
            return report, 1 if remaining else 0


def main() -> int:
    """Uruchamia dry-run albo kontrolowaną naprawę i zwraca raport JSON."""
    args = _parser().parse_args()
    report, exit_code = asyncio.run(_run(args))
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
