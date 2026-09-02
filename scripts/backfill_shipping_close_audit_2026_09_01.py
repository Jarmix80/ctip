#!/usr/bin/env python3
"""Odtwarza dwa brakujące wpisy audytu po incydencie zamknięcia Shipping."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from app.core.config import settings  # noqa: E402
from app.db.session import AsyncSessionLocal, engine  # noqa: E402
from app.models import (  # noqa: E402
    AdminAuditLog,
    ShippingCase,
    ShippingDayClose,
    ShippingEvent,
)

INCIDENT_ID = "shipping-close-2026-09-01"
DEFAULT_ORDER_TABLE_ID = 83540
DEFAULT_DAY_CLOSE_ID = 2
DEFAULT_CLIENT_IP = "192.168.0.23"
EXPECTED_OPERATOR_ID = 18
APPLY_CONFIRMATION = "UZUPELNIJ AUDYT SHIPPING 2026-09-01"


class ShippingAuditBackfillError(RuntimeError):
    """Oznacza niejednoznaczne albo niezgodne dane źródłowe incydentu."""


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    normalized = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return normalized.isoformat()


async def _source_candidates(
    session: AsyncSession,
    *,
    order_table_id: int,
    day_close_id: int,
    client_ip: str,
    expected_operator_id: int,
) -> list[dict[str, Any]]:
    event_rows = (
        await session.execute(
            select(ShippingEvent, ShippingCase)
            .join(ShippingCase, ShippingCase.id == ShippingEvent.shipping_case_id)
            .where(
                ShippingCase.firebird_order_table_id == order_table_id,
                ShippingEvent.event_type == "courier_handover",
            )
            .order_by(ShippingEvent.id)
        )
    ).all()
    order_events = [
        (event, case)
        for event, case in event_rows
        if (event.payload or {}).get("scope") == "single_order"
    ]
    if len(order_events) != 1:
        raise ShippingAuditBackfillError(
            f"Oczekiwano jednego zdarzenia single_order dla {order_table_id}, znaleziono "
            f"{len(order_events)}."
        )
    order_event, order_case = order_events[0]
    if order_event.created_by != expected_operator_id:
        raise ShippingAuditBackfillError(
            f"Operator zdarzenia zlecenia to {order_event.created_by}, oczekiwano "
            f"{expected_operator_id}."
        )

    day_close = await session.get(ShippingDayClose, day_close_id)
    if day_close is None:
        raise ShippingAuditBackfillError(f"Nie znaleziono zamknięcia dnia id={day_close_id}.")
    if day_close.closed_by != expected_operator_id:
        raise ShippingAuditBackfillError(
            f"Operator zamknięcia dnia to {day_close.closed_by}, oczekiwano "
            f"{expected_operator_id}."
        )
    if day_close.status not in {"completed", "partial"}:
        raise ShippingAuditBackfillError(
            f"Zamknięcie dnia ma nieoczekiwany status {day_close.status}."
        )

    common = {
        "backfill": True,
        "incident_id": INCIDENT_ID,
        "backfill_reason": (
            "Operacja biznesowa została zatwierdzona, ale odpowiedź HTTP 500 przerwała "
            "pierwotny zapis admin_audit_log."
        ),
    }
    return [
        {
            "key": f"order-{order_table_id}",
            "action": "shipping_order_close",
            "user_id": expected_operator_id,
            "client_ip": client_ip,
            "payload": {
                **common,
                "backfill_key": f"order-{order_table_id}",
                "operation_occurred_at": _iso(order_event.created_at),
                "source_shipping_event_id": order_event.id,
                "order_table_id": order_table_id,
                "order_number": (
                    f"{order_case.firebird_order_id}/{order_case.firebird_order_year}"
                ),
                "status": "closed",
                "documents": (order_event.payload or {}).get("documents"),
                "notification_errors": (order_event.payload or {}).get("notification_errors"),
            },
        },
        {
            "key": f"day-close-{day_close_id}",
            "action": "shipping_day_close",
            "user_id": expected_operator_id,
            "client_ip": client_ip,
            "payload": {
                **common,
                "backfill_key": f"day-close-{day_close_id}",
                "operation_occurred_at": _iso(day_close.completed_at),
                "source_day_close_id": day_close.id,
                "business_date": day_close.business_date.isoformat(),
                "status": day_close.status,
                **dict(day_close.summary or {}),
            },
        },
    ]


async def backfill_shipping_close_audit(
    session: AsyncSession,
    *,
    apply: bool,
    order_table_id: int = DEFAULT_ORDER_TABLE_ID,
    day_close_id: int = DEFAULT_DAY_CLOSE_ID,
    client_ip: str = DEFAULT_CLIENT_IP,
    expected_operator_id: int = EXPECTED_OPERATOR_ID,
) -> dict[str, Any]:
    """Buduje i opcjonalnie zapisuje dwa idempotentne wpisy audytowe."""
    candidates = await _source_candidates(
        session,
        order_table_id=order_table_id,
        day_close_id=day_close_id,
        client_ip=client_ip,
        expected_operator_id=expected_operator_id,
    )
    existing_rows = (
        (
            await session.execute(
                select(AdminAuditLog).where(
                    AdminAuditLog.action.in_([candidate["action"] for candidate in candidates])
                )
            )
        )
        .scalars()
        .all()
    )
    existing_keys = {
        str((row.payload or {}).get("backfill_key"))
        for row in existing_rows
        if (row.payload or {}).get("incident_id") == INCIDENT_ID
    }
    missing = [candidate for candidate in candidates if candidate["key"] not in existing_keys]

    if apply:
        now = datetime.now(UTC)
        for candidate in missing:
            session.add(
                AdminAuditLog(
                    created_at=now,
                    user_id=candidate["user_id"],
                    action=candidate["action"],
                    client_ip=candidate["client_ip"],
                    payload=candidate["payload"],
                )
            )
        await session.commit()
    else:
        await session.rollback()

    return {
        "mode": "apply" if apply else "dry-run",
        "incident_id": INCIDENT_ID,
        "candidate_count": len(candidates),
        "existing_count": len(candidates) - len(missing),
        "created_count": len(missing) if apply else 0,
        "would_create_count": 0 if apply else len(missing),
        "entries": [
            {
                "key": candidate["key"],
                "action": candidate["action"],
                "status": "existing" if candidate["key"] in existing_keys else "missing",
                "operation_occurred_at": candidate["payload"]["operation_occurred_at"],
            }
            for candidate in candidates
        ],
    }


def _assert_production_target() -> None:
    if settings.ctip_runtime_profile != "production":
        raise ShippingAuditBackfillError("Tryb --apply wymaga profilu produkcyjnego.")
    if settings.pg_host != "192.168.0.8" or settings.pg_database != "ctip":
        raise ShippingAuditBackfillError(
            "Tryb --apply wymaga bazy ctip na produkcyjnym hoście 192.168.0.8."
        )


def parse_args() -> argparse.Namespace:
    """Parsuje tryb kontrolowanego backfillu."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirmation", default="")
    return parser.parse_args()


async def _run() -> int:
    args = parse_args()
    try:
        if args.apply:
            _assert_production_target()
            if args.confirmation != APPLY_CONFIRMATION:
                raise ShippingAuditBackfillError(
                    f"Niepoprawna fraza potwierdzająca. Wymagana: {APPLY_CONFIRMATION}"
                )
        async with AsyncSessionLocal() as session:
            result = await backfill_shipping_close_audit(session, apply=args.apply)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    finally:
        await engine.dispose()


def main() -> int:
    """Uruchamia backfill i zwalnia pulę połączeń."""
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
