"""Worker trwałych, ręcznie uruchamianych audytów urządzeń."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select

from app.db.session import AsyncSessionLocal
from app.models import DeviceAuditItem, DeviceAuditRun, DeviceInventoryUnit
from app.services.device_audit import (
    build_device_audit_items,
    load_fresh_firebird_rows,
    load_fresh_sheet_rows,
    summarize_device_audit,
)
from app.services.firebird_runtime import load_firebird_runtime_config
from app.services.workflow_sheet_sync import load_workflow_sheet_runtime_config

logger = logging.getLogger(__name__)

_PROCESS_LOCK = asyncio.Lock()
_scheduler_task: asyncio.Task[None] | None = None
_scheduler_stop_event: asyncio.Event | None = None


async def _update_run(run_id: int, **values: Any) -> None:
    async with AsyncSessionLocal() as session:
        run = await session.get(DeviceAuditRun, run_id)
        if run is None:
            return
        for key, value in values.items():
            setattr(run, key, value)
        run.updated_at = datetime.now(UTC)
        await session.commit()


async def _load_ctip_rows() -> list[dict[str, Any]]:
    async with AsyncSessionLocal() as session:
        units = list(
            (await session.execute(select(DeviceInventoryUnit).order_by(DeviceInventoryUnit.id)))
            .scalars()
            .all()
        )
        return [
            {
                "ctip_unit_id": unit.id,
                "source_row": unit.source_row,
                "producer": (unit.snapshot or {}).get("producer"),
                "model": (unit.snapshot or {}).get("model"),
                "serial": unit.serial,
                "ewidencja": unit.ewidencja,
                "sheet_sync_status": unit.sheet_sync_status,
                "sheet_row": unit.sheet_row,
            }
            for unit in units
        ]


async def _claim_run() -> int | None:
    async with AsyncSessionLocal() as session:
        stale_before = datetime.now(UTC) - timedelta(hours=2)
        stale_runs = list(
            (
                await session.execute(
                    select(DeviceAuditRun).where(
                        DeviceAuditRun.status == "running",
                        DeviceAuditRun.updated_at < stale_before,
                    )
                )
            )
            .scalars()
            .all()
        )
        for stale in stale_runs:
            stale.status = "failed"
            stale.phase = "Przerwano"
            stale.error_text = "Audyt został przerwany przed zakończeniem."
            stale.completed_at = datetime.now(UTC)
            stale.updated_at = datetime.now(UTC)

        run = (
            (
                await session.execute(
                    select(DeviceAuditRun)
                    .where(DeviceAuditRun.status == "pending")
                    .order_by(DeviceAuditRun.created_at, DeviceAuditRun.id)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .first()
        )
        if run is None:
            if stale_runs:
                await session.commit()
            return None
        now = datetime.now(UTC)
        run.status = "running"
        run.phase = "Przygotowanie źródeł"
        run.started_at = now
        run.updated_at = now
        run.error_text = None
        await session.commit()
        return run.id


async def _persist_result(
    run_id: int,
    *,
    items: list[dict[str, Any]],
    source_snapshot: dict[str, Any],
) -> None:
    async with AsyncSessionLocal() as session:
        run = await session.get(DeviceAuditRun, run_id)
        if run is None:
            return
        await session.execute(delete(DeviceAuditItem).where(DeviceAuditItem.run_id == run_id))
        session.add_all(
            [
                DeviceAuditItem(
                    run_id=run_id,
                    canonical_key=item["canonical_key"],
                    producer=item.get("producer"),
                    model=item.get("model"),
                    serial=item.get("serial"),
                    ewidencja=item.get("ewidencja"),
                    source_row=item.get("source_row"),
                    sheet_row=item.get("sheet_row"),
                    machine_id=item.get("machine_id"),
                    ctip_unit_id=item.get("ctip_unit_id"),
                    sheet_present=bool(item.get("sheet_present")),
                    warehouse_present=bool(item.get("warehouse_present")),
                    machine_present=bool(item.get("machine_present")),
                    ctip_present=bool(item.get("ctip_present")),
                    result_status=item["result_status"],
                    issue_codes=list(item.get("issue_codes") or []),
                    issue_summary=item.get("issue_summary"),
                    source_details=dict(item.get("source_details") or {}),
                )
                for item in items
            ]
        )
        now = datetime.now(UTC)
        run.status = "completed"
        run.phase = "Zakończono"
        run.processed_items = len(items)
        run.total_items = len(items)
        run.summary = summarize_device_audit(items)
        run.source_snapshot = source_snapshot
        run.completed_at = now
        run.updated_at = now
        await session.commit()

        retained_ids = list(
            (
                await session.execute(
                    select(DeviceAuditRun.id)
                    .order_by(DeviceAuditRun.created_at.desc(), DeviceAuditRun.id.desc())
                    .limit(20)
                )
            )
            .scalars()
            .all()
        )
        if retained_ids:
            await session.execute(
                delete(DeviceAuditRun).where(DeviceAuditRun.id.not_in(retained_ids))
            )
            await session.commit()


async def process_device_audit_once() -> dict[str, Any]:
    """Przetwarza najwyżej jeden oczekujący audyt."""
    async with _PROCESS_LOCK:
        run_id = await _claim_run()
        if run_id is None:
            return {"processed": 0}
        try:
            async with AsyncSessionLocal() as session:
                firebird_config = await load_firebird_runtime_config(session)
                sheet_config = await load_workflow_sheet_runtime_config(session)

            await _update_run(run_id, phase="Odczyt Firebird")
            warehouse_rows, machine_rows, firebird_snapshot = await asyncio.to_thread(
                load_fresh_firebird_rows,
                firebird_config,
            )
            await _update_run(
                run_id,
                phase="Odczyt Google Sheets",
                total_items=len(warehouse_rows) + len(machine_rows),
            )
            sheet_rows, sheet_snapshot = await asyncio.to_thread(
                load_fresh_sheet_rows,
                sheet_config,
            )
            await _update_run(run_id, phase="Odczyt rejestru CTIP")
            ctip_rows = await _load_ctip_rows()
            await _update_run(
                run_id,
                phase="Porównywanie danych",
                total_items=(
                    len(warehouse_rows) + len(machine_rows) + len(sheet_rows) + len(ctip_rows)
                ),
            )
            items = await asyncio.to_thread(
                build_device_audit_items,
                sheet_rows=sheet_rows,
                warehouse_rows=warehouse_rows,
                machine_rows=machine_rows,
                ctip_rows=ctip_rows,
            )
            await _persist_result(
                run_id,
                items=items,
                source_snapshot={
                    "firebird": firebird_snapshot,
                    "sheet": sheet_snapshot,
                    "ctip": {"rows": len(ctip_rows)},
                },
            )
            return {"processed": 1, "run_id": run_id, "items": len(items)}
        except Exception as exc:  # noqa: BLE001
            logger.exception("Błąd audytu urządzeń.")
            await _update_run(
                run_id,
                status="failed",
                phase="Błąd",
                error_text=str(exc).strip()[:4000] or "Nieznany błąd audytu.",
                completed_at=datetime.now(UTC),
            )
            return {"processed": 1, "run_id": run_id, "error": str(exc)}


async def _scheduler_loop(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            await process_device_audit_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("Błąd cyklu workera audytu urządzeń.")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=5)
        except TimeoutError:
            continue


async def start_device_audit_scheduler() -> None:
    """Uruchamia worker audytu urządzeń."""
    global _scheduler_stop_event, _scheduler_task
    if _scheduler_task is not None and not _scheduler_task.done():
        return
    _scheduler_stop_event = asyncio.Event()
    _scheduler_task = asyncio.create_task(
        _scheduler_loop(_scheduler_stop_event),
        name="device-audit",
    )


async def stop_device_audit_scheduler() -> None:
    """Zatrzymuje worker audytu urządzeń."""
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
    "process_device_audit_once",
    "start_device_audit_scheduler",
    "stop_device_audit_scheduler",
]
