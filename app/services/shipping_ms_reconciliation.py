"""Automatyczne uzgadnianie ręcznych zmian aktywnych zleceń Shipping w MS."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import and_, or_, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.models import ShippingCase, ShippingEvent, ShippingShipment
from app.services.shipping_archive import archive_shipping_shipment
from app.services.shipping_firebird import inspect_active_shipping_orders, shipping_document_mode

logger = logging.getLogger(__name__)

CONFLICT_PREFIX = "Ręczna zmiana MS: "
CONFLICT_EVENT = "external_manual_change_conflict"
RESTORED_EVENT = "external_manual_change_restored"
RECONCILED_EVENT = "external_closure_reconciled"
_POSTGRES_LOCK_KEY = 7_341_109_202_609_08
_PROCESS_LOCK = asyncio.Lock()
_scheduler_task: asyncio.Task[None] | None = None
_scheduler_stop_event: asyncio.Event | None = None
_last_status: dict[str, Any] = {
    "status": "not_started",
    "trigger_type": None,
    "started_at": None,
    "completed_at": None,
    "scanned_count": 0,
    "healthy_count": 0,
    "reconciled_count": 0,
    "restored_count": 0,
    "conflict_count": 0,
    "error_text": None,
}


class ShippingMsReconciliationBusyError(RuntimeError):
    """Oznacza, że inny proces wykonuje już uzgadnianie Shipping z MS."""


def shipping_ms_reconciliation_status() -> dict[str, Any]:
    """Zwraca bezpieczny stan harmonogramu i ostatniego cyklu uzgadniania."""
    return {
        "enabled": bool(settings.shipping_ms_reconcile_enabled),
        "interval_seconds": int(settings.shipping_ms_reconcile_interval_seconds),
        "batch_limit": int(settings.shipping_ms_reconcile_batch_limit),
        "running": _PROCESS_LOCK.locked(),
        **deepcopy(_last_status),
    }


def _candidate_payload(shipment: ShippingShipment) -> dict[str, Any]:
    case = shipment.shipping_case
    return {
        "shipment_id": int(shipment.id),
        "order_table_id": int(case.firebird_order_table_id),
        "order_id": int(case.firebird_order_id),
        "order_year": int(case.firebird_order_year),
        "client_id": int(case.firebird_client_id),
        "tracking_number": shipment.tracking_number,
        "document_mode": shipping_document_mode(
            order_kind=case.order_kind,
            invoice_required=case.invoice_required,
        ),
        "items": [
            {
                "warehouse_item_id": int(item.firebird_warehouse_item_id),
                "warehouse_id": int(item.warehouse_id),
                "quantity": str(item.quantity),
                "price_net": str(item.price_net),
                "vat_rate": str(item.vat_rate),
            }
            for item in case.items
        ],
    }


async def _latest_reconciliation_events(
    session: AsyncSession,
    shipment_ids: list[int],
) -> dict[int, ShippingEvent]:
    if not shipment_ids:
        return {}
    events = list(
        (
            await session.execute(
                select(ShippingEvent)
                .where(
                    ShippingEvent.shipment_id.in_(shipment_ids),
                    ShippingEvent.event_type.in_(
                        (CONFLICT_EVENT, RESTORED_EVENT, RECONCILED_EVENT)
                    ),
                )
                .order_by(ShippingEvent.shipment_id, ShippingEvent.id.desc())
            )
        )
        .scalars()
        .all()
    )
    latest: dict[int, ShippingEvent] = {}
    for event in events:
        if event.shipment_id is not None:
            latest.setdefault(int(event.shipment_id), event)
    return latest


def _previous_statuses(
    shipment: ShippingShipment,
    latest_event: ShippingEvent | None,
) -> tuple[str, str, str]:
    payload = (
        latest_event.payload if latest_event and isinstance(latest_event.payload, dict) else {}
    )
    shipment_status = str(payload.get("previous_shipment_status") or shipment.status)
    if shipment_status not in {"label_ready", "handed_over"}:
        shipment_status = "handed_over" if shipment.handed_over_at else "label_ready"
    case_status = str(payload.get("previous_case_status") or shipment.shipping_case.status)
    if case_status not in {"shipment_created", "handed_over"}:
        case_status = "handed_over" if shipment_status == "handed_over" else "shipment_created"
    firebird_status = str(payload.get("previous_firebird_status") or shipment.firebird_status)
    if firebird_status == "reconcile_required":
        firebird_status = "written"
    return shipment_status, case_status, firebird_status


def _event_is_duplicate(
    event: ShippingEvent | None,
    *,
    event_type: str,
    state_token: str,
) -> bool:
    return bool(
        event
        and event.event_type == event_type
        and isinstance(event.payload, dict)
        and event.payload.get("state_token") == state_token
    )


def _skip_external_notifications(shipment: ShippingShipment) -> None:
    """Oznacza niewysłane wiadomości jako świadomie pominięte po zamknięciu poza CTIP."""
    if shipment.notification_sms_status != "sent":
        shipment.notification_sms_status = "skipped_external"
    if shipment.notification_email_status != "sent":
        shipment.notification_email_status = "skipped_external"
    shipment.notification_error = None


async def _apply_external_closure(
    session: AsyncSession,
    *,
    shipment: ShippingShipment,
    inspection: dict[str, Any],
    trigger_type: str,
    user_id: int | None,
) -> None:
    closed_at_text = str(inspection.get("closed_at") or "")
    if not closed_at_text:
        raise RuntimeError("Zgodne zamknięcie MS nie ma daty wymaganej przez archiwum Shipping.")
    closed_at = datetime.fromisoformat(closed_at_text)
    if closed_at.tzinfo is None:
        closed_at = closed_at.replace(tzinfo=UTC)
    documents = dict(inspection.get("documents") or {})
    shipment.firebird_rw_id = documents.get("rw_id")
    shipment.firebird_rw_number = documents.get("rw_number")
    shipment.firebird_wz_id = documents.get("wz_id")
    shipment.firebird_wz_number = documents.get("wz_number")
    shipment.firebird_invoice_id = documents.get("invoice_id")
    shipment.firebird_invoice_number = documents.get("invoice_number")
    shipment.shipping_case.status = "closed"
    shipment.status = "closed"
    shipment.firebird_status = "written"
    shipment.firebird_error = None
    shipment.handed_over_at = shipment.handed_over_at or closed_at
    shipment.closed_at = closed_at
    shipment.updated_at = datetime.now(UTC)
    shipment.shipping_case.updated_at = shipment.updated_at
    _skip_external_notifications(shipment)
    await archive_shipping_shipment(
        session,
        shipment=shipment,
        closed_by=None,
        closed_at=closed_at,
        closing_operator_name=inspection.get("closing_operator") or "Operator MS",
    )
    session.add(
        ShippingEvent(
            shipping_case_id=shipment.shipping_case_id,
            shipment_id=shipment.id,
            event_type=RECONCILED_EVENT,
            payload={
                "source": trigger_type,
                "state_token": inspection["state_token"],
                "external_state": inspection["snapshot"],
                "documents": documents,
                "notifications": {
                    "sms": shipment.notification_sms_status,
                    "email": shipment.notification_email_status,
                    "reason": "Zamknięcie wykonano ręcznie w Menadżerze Serwisu.",
                },
            },
            created_by=user_id,
            created_at=datetime.now(UTC),
        )
    )


def _apply_conflict(
    session: AsyncSession,
    *,
    shipment: ShippingShipment,
    inspection: dict[str, Any],
    latest_event: ShippingEvent | None,
    trigger_type: str,
    user_id: int | None,
) -> None:
    message = f"{CONFLICT_PREFIX}{inspection['message']}"[:4000]
    previous_shipment, previous_case, previous_firebird = _previous_statuses(
        shipment,
        latest_event,
    )
    shipment.status = "reconcile_required"
    shipment.firebird_status = "reconcile_required"
    shipment.firebird_error = message
    shipment.updated_at = datetime.now(UTC)
    shipment.shipping_case.status = "reconcile_required"
    shipment.shipping_case.updated_at = shipment.updated_at
    if not _event_is_duplicate(
        latest_event,
        event_type=CONFLICT_EVENT,
        state_token=inspection["state_token"],
    ):
        session.add(
            ShippingEvent(
                shipping_case_id=shipment.shipping_case_id,
                shipment_id=shipment.id,
                event_type=CONFLICT_EVENT,
                payload={
                    "source": trigger_type,
                    "state_token": inspection["state_token"],
                    "message": inspection["message"],
                    "external_state": inspection["snapshot"],
                    "previous_shipment_status": previous_shipment,
                    "previous_case_status": previous_case,
                    "previous_firebird_status": previous_firebird,
                },
                created_by=user_id,
                created_at=datetime.now(UTC),
            )
        )


def _restore_healthy_state(
    session: AsyncSession,
    *,
    shipment: ShippingShipment,
    inspection: dict[str, Any],
    latest_event: ShippingEvent,
    trigger_type: str,
    user_id: int | None,
) -> None:
    shipment_status, case_status, firebird_status = _previous_statuses(shipment, latest_event)
    shipment.status = shipment_status
    shipment.firebird_status = firebird_status
    shipment.firebird_error = None
    shipment.updated_at = datetime.now(UTC)
    shipment.shipping_case.status = case_status
    shipment.shipping_case.updated_at = shipment.updated_at
    session.add(
        ShippingEvent(
            shipping_case_id=shipment.shipping_case_id,
            shipment_id=shipment.id,
            event_type=RESTORED_EVENT,
            payload={
                "source": trigger_type,
                "state_token": inspection["state_token"],
                "restored_shipment_status": shipment_status,
                "restored_case_status": case_status,
                "restored_firebird_status": firebird_status,
            },
            created_by=user_id,
            created_at=datetime.now(UTC),
        )
    )


async def reconcile_active_shipping_shipments(
    session: AsyncSession,
    *,
    apply: bool,
    trigger_type: str,
    user_id: int | None = None,
    batch_limit: int | None = None,
    inspector: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Porównuje aktywne przesyłki z MS i opcjonalnie uzgadnia bezpieczne przypadki."""
    limit = min(max(int(batch_limit or settings.shipping_ms_reconcile_batch_limit), 1), 1000)
    statement = (
        select(ShippingShipment)
        .options(selectinload(ShippingShipment.shipping_case).selectinload(ShippingCase.items))
        .where(
            ShippingShipment.provider_mode != "mock",
            ShippingShipment.tracking_number.is_not(None),
            or_(
                ShippingShipment.status.in_(("label_ready", "handed_over")),
                and_(
                    ShippingShipment.status == "reconcile_required",
                    ShippingShipment.firebird_error.like(f"{CONFLICT_PREFIX}%"),
                ),
            ),
        )
        .order_by(ShippingShipment.created_at, ShippingShipment.id)
        .limit(limit)
    )
    if apply:
        statement = statement.with_for_update(skip_locked=True)
    shipments = list((await session.execute(statement)).scalars().all())
    candidates = [_candidate_payload(shipment) for shipment in shipments]
    inspect = inspector or inspect_active_shipping_orders
    inspections = await asyncio.to_thread(inspect, candidates) if candidates else []
    inspections_by_order = {int(item["order_table_id"]): item for item in inspections}
    latest_events = await _latest_reconciliation_events(
        session,
        [int(shipment.id) for shipment in shipments],
    )
    summary: dict[str, Any] = {
        "status": "success",
        "trigger_type": trigger_type,
        "apply": bool(apply),
        "scanned_count": len(shipments),
        "healthy_count": 0,
        "reconciled_count": 0,
        "restored_count": 0,
        "conflict_count": 0,
        "items": [],
    }
    for shipment in shipments:
        case = shipment.shipping_case
        inspection = inspections_by_order.get(int(case.firebird_order_table_id))
        if inspection is None:
            inspection = {
                "order_table_id": case.firebird_order_table_id,
                "classification": "conflict",
                "message": "Odczyt Firebirda nie zwrócił wyniku dla aktywnego zlecenia.",
                "state_token": f"missing-{case.firebird_order_table_id}",
                "snapshot": {"order_table_id": case.firebird_order_table_id, "missing": True},
                "documents": {},
            }
        classification = str(inspection["classification"])
        latest_event = latest_events.get(int(shipment.id))
        action = "healthy"
        if classification == "external_closed":
            action = "reconcile"
            summary["reconciled_count"] += 1
            if apply:
                await _apply_external_closure(
                    session,
                    shipment=shipment,
                    inspection=inspection,
                    trigger_type=trigger_type,
                    user_id=user_id,
                )
        elif classification == "healthy":
            if shipment.status == "reconcile_required" and latest_event:
                action = "restore"
                summary["restored_count"] += 1
                if apply:
                    _restore_healthy_state(
                        session,
                        shipment=shipment,
                        inspection=inspection,
                        latest_event=latest_event,
                        trigger_type=trigger_type,
                        user_id=user_id,
                    )
            else:
                summary["healthy_count"] += 1
        else:
            action = "conflict"
            summary["conflict_count"] += 1
            if apply:
                _apply_conflict(
                    session,
                    shipment=shipment,
                    inspection=inspection,
                    latest_event=latest_event,
                    trigger_type=trigger_type,
                    user_id=user_id,
                )
        summary["items"].append(
            {
                "shipment_id": shipment.id,
                "order_table_id": case.firebird_order_table_id,
                "order_number": f"{case.firebird_order_id}/{case.firebird_order_year}",
                "action": action,
                "message": inspection["message"],
                "documents": inspection.get("documents") or {},
            }
        )
    if apply:
        await session.flush()
    return summary


@asynccontextmanager
async def _database_reconciliation_lock() -> AsyncIterator[bool]:
    connection: AsyncConnection | None = None
    acquired = True
    if engine.dialect.name == "postgresql":
        connection = await engine.connect()
        acquired = bool(
            await connection.scalar(
                text("SELECT pg_try_advisory_lock(:lock_key)"),
                {"lock_key": _POSTGRES_LOCK_KEY},
            )
        )
    try:
        yield acquired
    finally:
        if connection is not None:
            if acquired:
                await connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_key)"),
                    {"lock_key": _POSTGRES_LOCK_KEY},
                )
            await connection.close()


async def synchronize_shipping_ms(
    *,
    trigger_type: Literal["scheduler", "manual", "script"] = "manual",
    user_id: int | None = None,
    apply: bool = True,
) -> dict[str, Any]:
    """Uruchamia pojedynczy cykl pod blokadą procesu i bazy PostgreSQL."""
    global _last_status
    if _PROCESS_LOCK.locked():
        raise ShippingMsReconciliationBusyError("Uzgadnianie Shipping z MS jest już w toku.")
    async with _PROCESS_LOCK, _database_reconciliation_lock() as acquired:
        if not acquired:
            raise ShippingMsReconciliationBusyError(
                "Inny proces CTIP uzgadnia teraz aktywne zlecenia Shipping z MS."
            )
        started_at = datetime.now(UTC)
        _last_status = {
            **_last_status,
            "status": "processing",
            "trigger_type": trigger_type,
            "started_at": started_at.isoformat(),
            "completed_at": None,
            "error_text": None,
        }
        try:
            async with AsyncSessionLocal() as session:
                result = await reconcile_active_shipping_shipments(
                    session,
                    apply=apply,
                    trigger_type=trigger_type,
                    user_id=user_id,
                )
                if apply:
                    await session.commit()
                else:
                    await session.rollback()
        except Exception as exc:
            _last_status = {
                **_last_status,
                "status": "failed",
                "completed_at": datetime.now(UTC).isoformat(),
                "error_text": str(exc)[:4000],
            }
            raise
        _last_status = {
            **_last_status,
            "status": "success",
            "completed_at": datetime.now(UTC).isoformat(),
            "scanned_count": result["scanned_count"],
            "healthy_count": result["healthy_count"],
            "reconciled_count": result["reconciled_count"],
            "restored_count": result["restored_count"],
            "conflict_count": result["conflict_count"],
            "error_text": None,
        }
        return result


async def _scheduler_loop(stop_event: asyncio.Event) -> None:
    interval = max(int(settings.shipping_ms_reconcile_interval_seconds), 60)
    while not stop_event.is_set():
        try:
            await synchronize_shipping_ms(trigger_type="scheduler", apply=True)
        except ShippingMsReconciliationBusyError:
            logger.info("Pominięto cykl uzgadniania Shipping z MS, ponieważ trwa inny cykl.")
        except Exception:
            logger.exception("Błąd cyklicznego uzgadniania aktywnych zleceń Shipping z MS.")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except TimeoutError:
            continue


async def start_shipping_ms_reconciliation_scheduler() -> None:
    """Uruchamia harmonogram uzgadniania tylko po jawnym włączeniu konfiguracji."""
    global _scheduler_stop_event, _scheduler_task
    if _scheduler_task is not None and not _scheduler_task.done():
        return
    if not settings.shipping_ms_reconcile_enabled:
        return
    _scheduler_stop_event = asyncio.Event()
    _scheduler_task = asyncio.create_task(
        _scheduler_loop(_scheduler_stop_event),
        name="shipping-ms-reconciliation",
    )
    logger.info(
        "Uruchomiono uzgadnianie Shipping z MS co %s s.",
        settings.shipping_ms_reconcile_interval_seconds,
    )


async def stop_shipping_ms_reconciliation_scheduler() -> None:
    """Zatrzymuje harmonogram bez przerywania rozpoczętego cyklu."""
    global _scheduler_stop_event, _scheduler_task
    if _scheduler_stop_event is not None:
        _scheduler_stop_event.set()
    if _scheduler_task is not None:
        try:
            await asyncio.wait_for(_scheduler_task, timeout=15)
        except TimeoutError:
            _scheduler_task.cancel()
            try:
                await _scheduler_task
            except asyncio.CancelledError:
                pass
    _scheduler_stop_event = None
    _scheduler_task = None


__all__ = [
    "CONFLICT_EVENT",
    "CONFLICT_PREFIX",
    "RECONCILED_EVENT",
    "RESTORED_EVENT",
    "ShippingMsReconciliationBusyError",
    "reconcile_active_shipping_shipments",
    "shipping_ms_reconciliation_status",
    "start_shipping_ms_reconciliation_scheduler",
    "stop_shipping_ms_reconciliation_scheduler",
    "synchronize_shipping_ms",
]
