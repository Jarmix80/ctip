"""Automatyczne utrzymanie archiwum i zasobów GenForm."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models import FormRequest, FormWorkflowCase, FormWorkflowDevice
from app.services.contracts_dashboard import (
    load_firebird_runtime_config,
    use_firebird_runtime_config,
)
from app.services.contracts_proforma import delete_proforma_from_firebird
from app.services.contracts_workflow import (
    WORKFLOW_BUSINESS_STATUS_APPROVED_ORDER,
    WORKFLOW_BUSINESS_STATUS_REJECTED_GRENKE,
    build_workflow_device_key,
    list_form_workflow_devices,
    mark_workflow_resources_released,
    normalize_workflow_business_status,
    normalize_workflow_device_source_type,
)
from app.services.workflow_sheet_sync import (
    load_workflow_sheet_runtime_config,
    release_workflow_devices_from_sheet,
    use_workflow_sheet_runtime_config,
)

ARCHIVE_BUCKET_ACCEPTED = "accepted"
ARCHIVE_BUCKET_REJECTED = "rejected"
ARCHIVE_BUCKET_UNFILLED = "unfilled"
_scheduler_task: asyncio.Task | None = None
_stop_event: asyncio.Event | None = None


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _sheet_payload(device: FormWorkflowDevice) -> dict[str, Any]:
    snapshot = device.snapshot if isinstance(device.snapshot, dict) else {}
    row_value = device.source_row if device.source_row is not None else snapshot.get("row")
    source_type = normalize_workflow_device_source_type(
        snapshot.get("source_type") or device.source_type
    )
    return {
        "row": row_value,
        "source_row": row_value,
        "source_type": source_type,
        "source_key": build_workflow_device_key(source_type, row_value),
        "sheet_row": snapshot.get("sheet_row"),
        "producer": snapshot.get("producer") or device.producer or "",
        "model": snapshot.get("model") or device.model or "",
        "serial": snapshot.get("serial") or device.serial or "",
        "ewidencja": snapshot.get("ewidencja") or device.ewidencja or "",
        "index": snapshot.get("index") or device.ewidencja or "",
        "name": snapshot.get("name") or snapshot.get("description") or device.model or "",
    }


async def contracts_workflow_maintenance_tick() -> dict[str, int]:
    """Wykonuje pojedynczy przebieg automatycznego utrzymania GenForm."""
    now = datetime.now(UTC)
    released = 0
    archived = 0
    due_set = 0
    async with AsyncSessionLocal() as session:
        rejected_cases = list(
            (
                await session.execute(
                    select(FormWorkflowCase)
                    .where(FormWorkflowCase.resources_released_at.is_(None))
                    .where(FormWorkflowCase.resources_release_due_at.is_not(None))
                    .where(FormWorkflowCase.resources_release_due_at <= now)
                )
            )
            .scalars()
            .all()
        )
        for workflow_case in rejected_cases:
            devices = await list_form_workflow_devices(session, workflow_case_id=workflow_case.id)
            payloads = [_sheet_payload(device) for device in devices]
            if payloads:
                sheet_config = await load_workflow_sheet_runtime_config(session)
                try:
                    with use_workflow_sheet_runtime_config(sheet_config):
                        await asyncio.to_thread(
                            release_workflow_devices_from_sheet, devices=payloads
                        )
                except RuntimeError:
                    pass
            if workflow_case.proforma_firebird_id:
                firebird_config = await load_firebird_runtime_config(session)
                try:
                    with use_firebird_runtime_config(firebird_config):
                        await asyncio.to_thread(
                            delete_proforma_from_firebird,
                            int(workflow_case.proforma_firebird_id),
                        )
                    workflow_case.proforma_firebird_id = None
                    workflow_case.proforma_number = None
                    workflow_case.proforma_pdf_path = None
                except (RuntimeError, ValueError):
                    pass
            await mark_workflow_resources_released(
                session,
                workflow_case=workflow_case,
                updated_by=None,
                status_source="auto_release",
                note="Automatycznie zwolniono zasoby po terminie odmowy GRENKE.",
            )
            released += 1

        forms = list((await session.execute(select(FormRequest))).scalars().all())
        case_by_form_id = {
            case.form_request_id: case
            for case in (await session.execute(select(FormWorkflowCase))).scalars().all()
        }
        for form in forms:
            if form.archive_bucket:
                continue
            workflow_case = case_by_form_id.get(form.id)
            status_value = normalize_workflow_business_status(
                workflow_case.business_status if workflow_case else None
            )
            bucket = None
            if form.status != "SUBMITTED":
                expires_at = _aware(form.token_expires_at)
                if expires_at <= now:
                    bucket = ARCHIVE_BUCKET_UNFILLED
                    if form.archive_due_at is None:
                        form.archive_due_at = expires_at + timedelta(days=14)
                        due_set += 1
            elif status_value == WORKFLOW_BUSINESS_STATUS_APPROVED_ORDER:
                bucket = ARCHIVE_BUCKET_ACCEPTED
                if form.archive_due_at is None:
                    form.archive_due_at = now + timedelta(days=14)
                    due_set += 1
            elif status_value == WORKFLOW_BUSINESS_STATUS_REJECTED_GRENKE:
                bucket = ARCHIVE_BUCKET_REJECTED
                if form.archive_due_at is None:
                    form.archive_due_at = now + timedelta(days=14)
                    due_set += 1
            if bucket and form.archive_due_at and _aware(form.archive_due_at) <= now:
                form.archive_bucket = bucket
                form.archived_at = now
                archived += 1
        await session.commit()
    return {"released": released, "archived": archived, "due_set": due_set}


async def _scheduler_loop() -> None:
    assert _stop_event is not None
    while not _stop_event.is_set():
        try:
            await contracts_workflow_maintenance_tick()
        except Exception:
            pass
        try:
            await asyncio.wait_for(
                _stop_event.wait(),
                timeout=max(60, settings.contracts_workflow_maintenance_interval_seconds),
            )
        except TimeoutError:
            continue


async def start_contracts_workflow_maintenance_scheduler() -> None:
    """Uruchamia scheduler utrzymania GenForm, jeśli nie działa."""
    global _scheduler_task, _stop_event
    if _scheduler_task is not None and not _scheduler_task.done():
        return
    _stop_event = asyncio.Event()
    _scheduler_task = asyncio.create_task(_scheduler_loop())


async def stop_contracts_workflow_maintenance_scheduler() -> None:
    """Zatrzymuje scheduler utrzymania GenForm."""
    global _scheduler_task, _stop_event
    if _scheduler_task is None:
        return
    if _stop_event is not None:
        _stop_event.set()
    await _scheduler_task
    _scheduler_task = None
    _stop_event = None
