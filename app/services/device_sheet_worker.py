"""Worker kolejki synchronizacji urządzeń z Google Sheets."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import aliased

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models import (
    DeviceInventoryEvent,
    DeviceInventoryUnit,
    DeviceManualReservation,
    DeviceSheetOutbox,
)
from app.services.device_registry import release_manual_reservation
from app.services.workflow_sheet_sync import (
    load_workflow_sheet_runtime_config,
    sync_device_inventory_to_sheet,
    use_workflow_sheet_runtime_config,
    workflow_sheet_sync_configured,
)

logger = logging.getLogger(__name__)

_PROCESS_LOCK = asyncio.Lock()
_scheduler_task: asyncio.Task[None] | None = None
_scheduler_stop_event: asyncio.Event | None = None


def _compose_sheet_payload(
    unit: DeviceInventoryUnit,
    queue_item: DeviceSheetOutbox,
) -> dict[str, Any]:
    """Łączy trwałą tożsamość egzemplarza z zakresem pojedynczej operacji."""
    snapshot = dict(unit.snapshot or {})
    payload = {
        "source_type": unit.source_type,
        "source_row": unit.source_row,
        "row": unit.source_row,
        "sheet_row": unit.sheet_row,
        "producer": snapshot.get("producer"),
        "model": snapshot.get("model"),
        "serial": unit.serial,
        "ewidencja": unit.ewidencja,
        "index": snapshot.get("warehouse_index") or unit.ewidencja,
        "price": (
            format(unit.purchase_price_net, "f") if unit.purchase_price_net is not None else ""
        ),
        "ms_id_maszyna": unit.firebird_machine_id,
        "ctip_env": settings.ctip_runtime_profile.upper(),
    }
    payload.update(dict(queue_item.payload or {}))
    return payload


def _retry_delay_seconds(attempt_count: int) -> int:
    """Wylicza ograniczone opóźnienie kolejnej próby."""
    exponent = max(0, min(int(attempt_count) - 1, 7))
    return min(3600, 30 * (2**exponent))


async def expire_device_manual_reservations() -> int:
    """Zwalnia przeterminowane rezerwacje i kolejkuje aktualizacje arkusza."""
    now = datetime.now(UTC)
    async with AsyncSessionLocal() as session:
        reservations = list(
            (
                await session.execute(
                    select(DeviceManualReservation)
                    .where(
                        DeviceManualReservation.released_at.is_(None),
                        DeviceManualReservation.expires_at <= now,
                    )
                    .order_by(DeviceManualReservation.expires_at.asc())
                    .limit(200)
                )
            )
            .scalars()
            .all()
        )
        expired_count = 0
        for reservation in reservations:
            unit = await session.get(DeviceInventoryUnit, reservation.unit_id)
            if unit is None:
                continue
            await release_manual_reservation(
                session,
                unit=unit,
                user_id=None,
                reason="Automatyczne zwolnienie po upływie terminu rezerwacji.",
                event_type="reservation_expired",
            )
            expired_count += 1
        if expired_count:
            await session.commit()
        return expired_count


async def process_device_sheet_outbox_once(*, limit: int | None = None) -> dict[str, Any]:
    """Przetwarza gotowe zadania outboxu i zapisuje wynik każdej próby."""
    async with _PROCESS_LOCK:
        async with AsyncSessionLocal() as session:
            config = await load_workflow_sheet_runtime_config(session)
            enabled, reason = workflow_sheet_sync_configured(config)
            if not enabled:
                return {
                    "processed": 0,
                    "completed": 0,
                    "failed": 0,
                    "reason": reason,
                }

            now = datetime.now(UTC)
            stale_lock = now - timedelta(minutes=15)
            batch_size = max(
                1,
                min(
                    int(limit or settings.device_sheet_outbox_batch_size),
                    200,
                ),
            )
            older_item = aliased(DeviceSheetOutbox)
            stmt = (
                select(DeviceSheetOutbox)
                .where(
                    or_(
                        (
                            (DeviceSheetOutbox.status == "pending")
                            & (DeviceSheetOutbox.next_attempt_at <= now)
                        ),
                        (
                            (DeviceSheetOutbox.status == "processing")
                            & (DeviceSheetOutbox.locked_at < stale_lock)
                        ),
                    ),
                    ~exists()
                    .where(
                        older_item.unit_id == DeviceSheetOutbox.unit_id,
                        older_item.id < DeviceSheetOutbox.id,
                        older_item.status != "completed",
                    )
                    .correlate(DeviceSheetOutbox),
                )
                .order_by(
                    DeviceSheetOutbox.next_attempt_at.asc(),
                    DeviceSheetOutbox.id.asc(),
                )
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
            queue_items = list((await session.execute(stmt)).scalars().all())

            claimed_at = datetime.now(UTC)
            for queue_item in queue_items:
                queue_item.status = "processing"
                queue_item.attempt_count = int(queue_item.attempt_count or 0) + 1
                queue_item.locked_at = claimed_at
                queue_item.updated_at = claimed_at
                unit = await session.get(DeviceInventoryUnit, queue_item.unit_id)
                if unit is not None:
                    unit.sheet_sync_status = "processing"
                    unit.sheet_sync_error = None
            if queue_items:
                await session.commit()

            completed = 0
            failed = 0
            for queue_item in queue_items:
                unit = await session.get(DeviceInventoryUnit, queue_item.unit_id)
                if unit is None:
                    queue_item.status = "failed"
                    queue_item.last_error = "Brak egzemplarza powiązanego z zadaniem outboxu."
                    queue_item.updated_at = datetime.now(UTC)
                    failed += 1
                    await session.commit()
                    continue

                payload = _compose_sheet_payload(unit, queue_item)
                try:
                    with use_workflow_sheet_runtime_config(config):
                        result = await asyncio.to_thread(
                            sync_device_inventory_to_sheet,
                            operation_type=queue_item.operation_type,
                            payload=payload,
                        )
                    if not result.get("enabled"):
                        raise RuntimeError(
                            str(result.get("reason") or "Synchronizacja arkusza jest wyłączona.")
                        )
                except Exception as exc:  # noqa: BLE001
                    error_text = str(exc).strip()[:4000] or "Nieznany błąd Google Sheets."
                    is_final = queue_item.attempt_count >= queue_item.max_attempts
                    queue_item.status = "failed" if is_final else "pending"
                    queue_item.last_error = error_text
                    queue_item.locked_at = None
                    queue_item.updated_at = datetime.now(UTC)
                    queue_item.next_attempt_at = datetime.now(UTC) + timedelta(
                        seconds=_retry_delay_seconds(queue_item.attempt_count)
                    )
                    unit.sheet_sync_status = "failed" if is_final else "pending"
                    unit.sheet_sync_error = error_text
                    session.add(
                        DeviceInventoryEvent(
                            unit_id=unit.id,
                            event_type="sheet_sync_failed",
                            created_by=None,
                            payload={
                                "outbox_id": queue_item.id,
                                "operation_type": queue_item.operation_type,
                                "attempt": queue_item.attempt_count,
                                "final": is_final,
                                "error": error_text,
                            },
                        )
                    )
                    failed += 1
                    await session.commit()
                    continue

                completed_at = datetime.now(UTC)
                queue_item.status = "completed"
                queue_item.last_error = None
                queue_item.locked_at = None
                queue_item.updated_at = completed_at
                queue_item.completed_at = completed_at
                unit.sheet_row = int(result["sheet_row"])
                unit.sheet_sync_status = "synced"
                unit.sheet_sync_error = None
                unit.updated_at = completed_at
                session.add(
                    DeviceInventoryEvent(
                        unit_id=unit.id,
                        event_type="sheet_sync_completed",
                        created_by=None,
                        payload={
                            "outbox_id": queue_item.id,
                            "operation_type": queue_item.operation_type,
                            "sheet_row": unit.sheet_row,
                            "action": result.get("action"),
                        },
                    )
                )
                completed += 1
                await session.commit()

            return {
                "processed": len(queue_items),
                "completed": completed,
                "failed": failed,
                "reason": None,
            }


async def retry_device_sheet_outbox(queue_item_id: int) -> DeviceSheetOutbox | None:
    """Przywraca nieudane zadanie do kolejki bez zerowania historii prób."""
    async with AsyncSessionLocal() as session:
        queue_item = await session.get(DeviceSheetOutbox, queue_item_id)
        if queue_item is None:
            return None
        if queue_item.status == "completed":
            return queue_item
        queue_item.status = "pending"
        queue_item.next_attempt_at = datetime.now(UTC)
        queue_item.locked_at = None
        queue_item.last_error = None
        if queue_item.attempt_count >= queue_item.max_attempts:
            queue_item.max_attempts = queue_item.attempt_count + 3
        unit = await session.get(DeviceInventoryUnit, queue_item.unit_id)
        if unit is not None:
            unit.sheet_sync_status = "pending"
            unit.sheet_sync_error = None
        await session.commit()
        return queue_item


async def _scheduler_loop(stop_event: asyncio.Event) -> None:
    interval = max(15, int(settings.device_sheet_outbox_interval_seconds))
    while not stop_event.is_set():
        try:
            await expire_device_manual_reservations()
            await process_device_sheet_outbox_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("Błąd cyklu kolejki Google Sheets urządzeń.")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except TimeoutError:
            continue


async def start_device_sheet_outbox_scheduler() -> None:
    """Uruchamia pojedynczy scheduler kolejki urządzeń."""
    global _scheduler_stop_event, _scheduler_task
    if _scheduler_task is not None and not _scheduler_task.done():
        return
    _scheduler_stop_event = asyncio.Event()
    _scheduler_task = asyncio.create_task(
        _scheduler_loop(_scheduler_stop_event),
        name="device-sheet-outbox",
    )


async def stop_device_sheet_outbox_scheduler() -> None:
    """Kończy scheduler i oczekuje na zamknięcie zadania."""
    global _scheduler_stop_event, _scheduler_task
    if _scheduler_task is None:
        return
    if _scheduler_stop_event is not None:
        _scheduler_stop_event.set()
    _scheduler_task.cancel()
    try:
        await _scheduler_task
    except asyncio.CancelledError:
        pass
    _scheduler_task = None
    _scheduler_stop_event = None


__all__ = [
    "expire_device_manual_reservations",
    "process_device_sheet_outbox_once",
    "retry_device_sheet_outbox",
    "start_device_sheet_outbox_scheduler",
    "stop_device_sheet_outbox_scheduler",
]
