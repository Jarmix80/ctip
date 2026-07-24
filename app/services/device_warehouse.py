"""Scalony odczyt magazynu urządzeń z Firebird, CTIP i Google Sheets."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    DeviceCounterReading,
    DeviceInventoryEvent,
    DeviceInventoryUnit,
    DeviceManualReservation,
    DeviceSheetOutbox,
    FormRequest,
    FormWorkflowCase,
    FormWorkflowDevice,
    WorkflowSheetStatusCache,
)
from app.services.device_registry import DEVICE_SOURCE_TYPE, normalize_device_identity


def _source_row(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _optional_bool(value: Any) -> bool | None:
    """Normalizuje opcjonalną flagę logiczną pochodzącą ze źródła Firebird."""
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().upper()
    if normalized in {"1", "TAK", "TRUE", "Y"}:
        return True
    if normalized in {"0", "NIE", "FALSE", "N"}:
        return False
    return None


def _unique_items_by_identity(
    items: list[Any],
    identity_getter,
) -> dict[str, Any]:
    unique: dict[str, Any] = {}
    duplicates: set[str] = set()
    for item in items:
        identity = normalize_device_identity(identity_getter(item))
        if not identity:
            continue
        if identity in unique:
            duplicates.add(identity)
            continue
        unique[identity] = item
    for identity in duplicates:
        unique.pop(identity, None)
    return unique


def _build_fallback_cache_by_source(
    firebird_rows: list[dict[str, Any]],
    cache_rows: list[WorkflowSheetStatusCache],
) -> dict[int, WorkflowSheetStatusCache]:
    """Dopasowuje historyczny cache po unikalnym serialu, a następnie ewidencji."""
    fallback_rows = [row for row in cache_rows if row.source_row is None]
    cache_by_serial = _unique_items_by_identity(fallback_rows, lambda row: row.serial)
    cache_by_index = _unique_items_by_identity(fallback_rows, lambda row: row.device_index)
    source_by_serial = _unique_items_by_identity(
        firebird_rows,
        lambda row: row.get("serial"),
    )
    source_by_index = _unique_items_by_identity(
        firebird_rows,
        lambda row: row.get("ewidencja") or row.get("index"),
    )

    matched: dict[int, WorkflowSheetStatusCache] = {}
    used_cache_ids: set[int] = set()
    for identity, source in source_by_serial.items():
        cache = cache_by_serial.get(identity)
        source_row = _source_row(source.get("row"))
        if cache is None or source_row is None:
            continue
        matched[source_row] = cache
        used_cache_ids.add(cache.id)

    for identity, source in source_by_index.items():
        source_row = _source_row(source.get("row"))
        cache = cache_by_index.get(identity)
        if (
            cache is None
            or source_row is None
            or source_row in matched
            or cache.id in used_cache_ids
        ):
            continue
        source_serial = normalize_device_identity(source.get("serial"))
        cache_serial = normalize_device_identity(cache.serial)
        if source_serial and cache_serial and source_serial != cache_serial:
            continue
        matched[source_row] = cache
        used_cache_ids.add(cache.id)
    return matched


async def build_device_warehouse_payload(
    session: AsyncSession,
    *,
    firebird_rows: list[dict[str, Any]],
    query: str | None = None,
    reservation_filter: str | None = None,
    sheet_filter: str | None = None,
    page: int = 1,
    page_size: int = 100,
) -> dict[str, Any]:
    """Scala stan magazynu z rejestrem, licznikami, rezerwacjami i cache arkusza."""
    source_rows = {
        row_id for item in firebird_rows if (row_id := _source_row(item.get("row"))) is not None
    }
    if not source_rows:
        return {
            "items": [],
            "total": 0,
            "page": 1,
            "page_size": page_size,
            "pages": 0,
            "summary": {
                "available": 0,
                "flow_reserved": 0,
                "manual_reserved": 0,
                "sheet_errors": 0,
                "audit_only": 0,
            },
        }

    units = list(
        (
            await session.execute(
                select(DeviceInventoryUnit).where(
                    DeviceInventoryUnit.source_type == DEVICE_SOURCE_TYPE,
                )
            )
        )
        .scalars()
        .all()
    )
    units_by_source = {unit.source_row: unit for unit in units}
    units_by_serial = _unique_items_by_identity(units, lambda unit: unit.serial)
    units_by_index = _unique_items_by_identity(units, lambda unit: unit.ewidencja)
    relevant_unit_ids: set[int] = set()
    for source in firebird_rows:
        source_row = _source_row(source.get("row"))
        unit = units_by_source.get(source_row) if source_row is not None else None
        if unit is None:
            unit = units_by_serial.get(normalize_device_identity(source.get("serial")))
        if unit is None:
            unit = units_by_index.get(
                normalize_device_identity(source.get("ewidencja") or source.get("index"))
            )
        if unit is not None:
            relevant_unit_ids.add(unit.id)
    unit_ids = sorted(relevant_unit_ids)

    cache_rows = list(
        (
            await session.execute(
                select(WorkflowSheetStatusCache).where(
                    WorkflowSheetStatusCache.source_type == DEVICE_SOURCE_TYPE,
                )
            )
        )
        .scalars()
        .all()
    )
    cache_by_source = {row.source_row: row for row in cache_rows if row.source_row is not None}
    fallback_cache_by_source = _build_fallback_cache_by_source(
        firebird_rows,
        cache_rows,
    )

    flow_rows = (
        await session.execute(
            select(FormWorkflowDevice, FormWorkflowCase, FormRequest)
            .join(FormWorkflowCase, FormWorkflowCase.id == FormWorkflowDevice.workflow_case_id)
            .join(FormRequest, FormRequest.id == FormWorkflowCase.form_request_id)
            .where(
                FormWorkflowDevice.source_type == DEVICE_SOURCE_TYPE,
                FormWorkflowDevice.source_row.in_(source_rows),
                FormRequest.status == "SUBMITTED",
                FormWorkflowCase.resources_released_at.is_(None),
            )
            .order_by(FormWorkflowCase.updated_at.desc())
        )
    ).all()
    flow_by_source: dict[int, dict[str, Any]] = {}
    for workflow_device, workflow_case, form_request in flow_rows:
        if workflow_device.source_row is None or workflow_device.source_row in flow_by_source:
            continue
        flow_by_source[workflow_device.source_row] = {
            "workflow_case_id": workflow_case.id,
            "form_request_id": form_request.id,
            "business_status": workflow_case.business_status,
            "reserved_for": _workflow_customer_name(workflow_case),
        }

    manual_by_unit: dict[int, DeviceManualReservation] = {}
    latest_notes_by_unit: dict[int, str] = {}
    latest_outbox_by_unit: dict[int, DeviceSheetOutbox] = {}
    latest_counters_by_unit: dict[int, dict[str, int]] = {}
    if unit_ids:
        manual_rows = list(
            (
                await session.execute(
                    select(DeviceManualReservation)
                    .where(
                        DeviceManualReservation.unit_id.in_(unit_ids),
                        DeviceManualReservation.released_at.is_(None),
                    )
                    .order_by(DeviceManualReservation.updated_at.desc())
                )
            )
            .scalars()
            .all()
        )
        manual_by_unit = {row.unit_id: row for row in manual_rows}

        note_rows = list(
            (
                await session.execute(
                    select(DeviceInventoryEvent)
                    .where(
                        DeviceInventoryEvent.unit_id.in_(unit_ids),
                        DeviceInventoryEvent.event_type == "note_changed",
                    )
                    .order_by(
                        DeviceInventoryEvent.created_at.desc(),
                        DeviceInventoryEvent.id.desc(),
                    )
                )
            )
            .scalars()
            .all()
        )
        for event in note_rows:
            if event.unit_id in latest_notes_by_unit:
                continue
            latest_notes_by_unit[event.unit_id] = str(
                (event.payload or {}).get("note") or ""
            ).strip()

        outbox_rows = list(
            (
                await session.execute(
                    select(DeviceSheetOutbox)
                    .where(DeviceSheetOutbox.unit_id.in_(unit_ids))
                    .order_by(
                        DeviceSheetOutbox.created_at.desc(),
                        DeviceSheetOutbox.id.desc(),
                    )
                )
            )
            .scalars()
            .all()
        )
        for queue_item in outbox_rows:
            latest_outbox_by_unit.setdefault(queue_item.unit_id, queue_item)

        counter_rows = list(
            (
                await session.execute(
                    select(DeviceCounterReading)
                    .where(
                        DeviceCounterReading.unit_id.in_(unit_ids),
                        DeviceCounterReading.applied_to_current.is_(True),
                    )
                    .order_by(
                        DeviceCounterReading.reading_at.desc(),
                        DeviceCounterReading.id.desc(),
                    )
                )
            )
            .scalars()
            .all()
        )
        for reading in counter_rows:
            values = latest_counters_by_unit.setdefault(reading.unit_id, {})
            for field in ("counter_bw", "counter_color", "counter_scan"):
                value = getattr(reading, field)
                if value is not None and field not in values:
                    values[field] = value

    now = datetime.now(UTC)
    items: list[dict[str, Any]] = []
    for source in firebird_rows:
        row_id = _source_row(source.get("row"))
        if row_id is None:
            continue
        unit = units_by_source.get(row_id)
        if unit is None:
            unit = units_by_serial.get(normalize_device_identity(source.get("serial")))
        if unit is None:
            unit = units_by_index.get(
                normalize_device_identity(source.get("ewidencja") or source.get("index"))
            )
        cache = cache_by_source.get(row_id) or fallback_cache_by_source.get(row_id)
        flow = flow_by_source.get(row_id)
        manual = manual_by_unit.get(unit.id) if unit is not None else None
        current_note = (
            (latest_notes_by_unit.get(unit.id, "") if unit is not None else "")
            or (cache.sheet_notes if cache is not None else "")
            or ""
        )
        latest_counter = latest_counters_by_unit.get(unit.id, {}) if unit is not None else {}
        counter_bw = (
            latest_counter["counter_bw"]
            if "counter_bw" in latest_counter
            else (cache.counter_bw if cache is not None else "")
        )
        counter_color = (
            latest_counter["counter_color"]
            if "counter_color" in latest_counter
            else (cache.counter_color if cache is not None else "")
        )
        counter_scan = (
            latest_counter["counter_scan"]
            if "counter_scan" in latest_counter
            else (cache.counter_scan if cache is not None else "")
        )
        is_color = _optional_bool(source.get("is_color"))
        if is_color is None:
            is_color = bool(str(counter_color).strip())

        reservation_kind = "none"
        reservation_status = "Brak rezerwacji"
        reservation_for = ""
        reservation_until = None
        reservation_reason = None
        reservation_locked = False
        if flow is not None:
            reservation_kind = "flow"
            reservation_status = "04. Rezerwacja GRENKE"
            reservation_for = str(
                flow.get("reserved_for") or f"Formularz {flow['form_request_id']}"
            )
            reservation_locked = True
        elif manual is not None:
            manual_expires_at = _as_utc(manual.expires_at)
            reservation_kind = "manual"
            reservation_status = (
                "Rezerwacja wygasła" if manual_expires_at <= now else "03. Rezerwacja ręczna"
            )
            reservation_for = manual.reserved_for
            reservation_until = _iso(manual_expires_at)
            reservation_reason = manual.reason
        elif cache is not None and cache.reservation_status:
            reservation_kind = "sheet"
            reservation_status = cache.reservation_status
            reservation_for = cache.reservation_grenke or ""
            reservation_until = (
                cache.reservation_until.isoformat() if cache.reservation_until else None
            )

        latest_outbox = latest_outbox_by_unit.get(unit.id) if unit is not None else None
        item = {
            "source_row": row_id,
            "unit_id": unit.id if unit is not None else None,
            "audit_only": unit is None,
            "producer": source.get("producer") or (cache.producer if cache else "") or "",
            "model": source.get("model") or (cache.model if cache else "") or "",
            "serial": source.get("serial") or (unit.serial if unit else "") or "",
            "ewidencja": source.get("ewidencja") or (unit.ewidencja if unit else "") or "",
            "warehouse_status": source.get("status") or "",
            "warehouse_quantity": source.get("warehouse_quantity") or "",
            "available_quantity": source.get("available_quantity") or "",
            "reserved_quantity": source.get("reserved_quantity") or "",
            "price_net": source.get("price_net") or "",
            "price_gross": source.get("price_gross") or "",
            "purchase_price_net": (
                format(unit.purchase_price_net, "f")
                if unit is not None and unit.purchase_price_net is not None
                else source.get("purchase_price_net") or ""
            ),
            "counter_bw": counter_bw,
            "counter_color": counter_color,
            "counter_scan": counter_scan,
            "is_color": is_color,
            "zeroing_status": cache.sheet_status if cache is not None else "",
            "note": current_note,
            "reservation_kind": reservation_kind,
            "reservation_status": reservation_status,
            "reservation_for": reservation_for,
            "reservation_until": reservation_until,
            "reservation_reason": reservation_reason,
            "reservation_locked": reservation_locked,
            "flow": flow,
            "sheet_row": (
                unit.sheet_row
                if unit is not None and unit.sheet_row is not None
                else (cache.sheet_row if cache is not None else None)
            ),
            "sheet_sync_status": (
                unit.sheet_sync_status if unit is not None else "history_not_registered"
            ),
            "sheet_sync_error": unit.sheet_sync_error if unit is not None else None,
            "latest_outbox": (
                {
                    "id": latest_outbox.id,
                    "status": latest_outbox.status,
                    "operation_type": latest_outbox.operation_type,
                    "attempt_count": latest_outbox.attempt_count,
                    "max_attempts": latest_outbox.max_attempts,
                    "last_error": latest_outbox.last_error,
                }
                if latest_outbox is not None
                else None
            ),
            "firebird_machine_id": (
                source.get("machine_id") or (unit.firebird_machine_id if unit is not None else None)
            ),
            "firebird_pz_id": unit.firebird_pz_id if unit is not None else None,
            "source_presence": {
                "sheet": cache is not None,
                "warehouse": True,
                "machine": bool(source.get("machine_present")),
                "ctip": unit is not None,
            },
        }
        items.append(item)

    summary = {
        "available": len(items),
        "flow_reserved": sum(item["reservation_kind"] == "flow" for item in items),
        "manual_reserved": sum(item["reservation_kind"] == "manual" for item in items),
        "sheet_errors": sum(item["sheet_sync_status"] == "failed" for item in items),
        "audit_only": sum(bool(item["audit_only"]) for item in items),
    }

    normalized_query = str(query or "").strip().casefold()
    if normalized_query:
        items = [
            item
            for item in items
            if normalized_query
            in " ".join(
                str(item.get(key) or "")
                for key in (
                    "producer",
                    "model",
                    "serial",
                    "ewidencja",
                    "reservation_for",
                    "note",
                )
            ).casefold()
        ]
    normalized_reservation = str(reservation_filter or "").strip().lower()
    if normalized_reservation and normalized_reservation != "all":
        items = [item for item in items if item["reservation_kind"] == normalized_reservation]
    normalized_sheet = str(sheet_filter or "").strip().lower()
    if normalized_sheet and normalized_sheet != "all":
        items = [item for item in items if item["sheet_sync_status"] == normalized_sheet]

    items.sort(
        key=lambda item: (
            str(item.get("producer") or "").casefold(),
            str(item.get("model") or "").casefold(),
            str(item.get("ewidencja") or "").casefold(),
        )
    )
    safe_page_size = max(10, min(int(page_size), 200))
    total = len(items)
    pages = (total + safe_page_size - 1) // safe_page_size
    safe_page = max(1, min(int(page), pages or 1))
    start = (safe_page - 1) * safe_page_size
    return {
        "items": items[start : start + safe_page_size],
        "total": total,
        "page": safe_page,
        "page_size": safe_page_size,
        "pages": pages,
        "summary": summary,
    }


def _workflow_customer_name(workflow_case: FormWorkflowCase) -> str:
    payload = (
        workflow_case.client_payload_snapshot
        if isinstance(workflow_case.client_payload_snapshot, dict)
        else {}
    )
    return str(payload.get("company_name") or "").strip()


async def serialize_device_events(
    session: AsyncSession,
    *,
    unit_id: int,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Zwraca dziennik operacyjny egzemplarza w formacie API."""
    rows = list(
        (
            await session.execute(
                select(DeviceInventoryEvent)
                .where(DeviceInventoryEvent.unit_id == unit_id)
                .order_by(
                    DeviceInventoryEvent.created_at.desc(),
                    DeviceInventoryEvent.id.desc(),
                )
                .limit(max(1, min(int(limit), 500)))
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": row.id,
            "event_type": row.event_type,
            "created_by": row.created_by,
            "payload": row.payload,
            "created_at": _iso(row.created_at),
        }
        for row in rows
    ]


__all__ = ["build_device_warehouse_payload", "serialize_device_events"]
