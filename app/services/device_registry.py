"""Rejestr idempotentnych przyjęć, uwag i rezerwacji urządzeń CTIP."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import (
    DeviceIntakeOperation,
    DeviceInventoryEvent,
    DeviceInventoryUnit,
    DeviceManualReservation,
    DeviceSheetOutbox,
    FormRequest,
    FormWorkflowCase,
    FormWorkflowDevice,
)

DEVICE_SOURCE_TYPE = "firebird_magazyn_28"
ACTIVE_FLOW_FORM_STATUS = "SUBMITTED"
AUTOMATED_PZ_NOTE = "dodana automatem PZ z CTIP"


class DeviceIdempotencyConflict(ValueError):
    """Sygnalizuje ponowne użycie UUID z innym żądaniem."""


class DeviceReservationConflict(ValueError):
    """Sygnalizuje kolizję rezerwacji urządzenia."""


def normalize_device_identity(value: str | None) -> str:
    """Normalizuje serial lub numer ewidencyjny do porównań unikalności."""
    return re.sub(r"[^A-Z0-9]", "", str(value or "").strip().upper())


def _json_default(value: Any) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime,)):
        return value.isoformat()
    return str(value)


def canonical_request_hash(payload: dict[str, Any]) -> str:
    """Buduje stabilny skrót żądania niezależny od kolejności kluczy JSON."""
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def find_intake_operation(
    session: AsyncSession,
    idempotency_key: str,
) -> DeviceIntakeOperation | None:
    """Wyszukuje operację przyjęcia po jej publicznym UUID."""
    stmt = select(DeviceIntakeOperation).where(
        DeviceIntakeOperation.idempotency_key == str(idempotency_key)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def begin_intake_operation(
    session: AsyncSession,
    *,
    idempotency_key: str,
    request_hash: str,
    request_payload: dict[str, Any],
    created_by: int,
    supplier_firebird_id: int,
    external_document: str | None,
    exception_reason: str | None,
) -> tuple[DeviceIntakeOperation, bool]:
    """Tworzy operację albo zwraca zgodny wpis istniejący."""
    existing = await find_intake_operation(session, idempotency_key)
    if existing is not None:
        if existing.request_hash != request_hash:
            raise DeviceIdempotencyConflict(
                "Klucz idempotencji został już użyty dla innego zestawu danych."
            )
        return existing, True

    operation = DeviceIntakeOperation(
        idempotency_key=str(idempotency_key),
        request_hash=request_hash,
        status="processing",
        created_by=created_by,
        supplier_firebird_id=supplier_firebird_id,
        external_document=external_document,
        exception_reason=exception_reason,
        request_payload=request_payload,
    )
    session.add(operation)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        concurrent = await find_intake_operation(session, idempotency_key)
        if concurrent is None:
            raise
        if concurrent.request_hash != request_hash:
            raise DeviceIdempotencyConflict(
                "Klucz idempotencji został równolegle użyty dla innego zestawu danych."
            ) from exc
        return concurrent, True
    return operation, False


def intake_operation_payload(operation: DeviceIntakeOperation) -> dict[str, Any]:
    """Mapuje operację przyjęcia do odpowiedzi API."""
    return {
        "id": operation.id,
        "idempotency_key": operation.idempotency_key,
        "status": operation.status,
        "created_by": operation.created_by,
        "supplier_firebird_id": operation.supplier_firebird_id,
        "external_document": operation.external_document,
        "exception_reason": operation.exception_reason,
        "firebird_pz_id": operation.firebird_pz_id,
        "firebird_pz_number": operation.firebird_pz_number,
        "result": operation.result_snapshot,
        "error": operation.error_text,
        "created_at": operation.created_at.isoformat() if operation.created_at else None,
        "updated_at": operation.updated_at.isoformat() if operation.updated_at else None,
        "completed_at": (operation.completed_at.isoformat() if operation.completed_at else None),
    }


async def mark_intake_operation_failed(
    session: AsyncSession,
    operation: DeviceIntakeOperation,
    error: str,
    *,
    reconcile_required: bool = False,
) -> None:
    """Zapisuje błąd operacji po bezpiecznym rollbacku Firebird."""
    operation.status = "reconcile_required" if reconcile_required else "failed"
    operation.error_text = str(error)[:4000]
    operation.updated_at = datetime.now(UTC)
    await session.flush()


async def enqueue_sheet_operation(
    session: AsyncSession,
    *,
    unit: DeviceInventoryUnit,
    operation_type: str,
    payload: dict[str, Any],
    idempotency_key: str | None = None,
) -> DeviceSheetOutbox:
    """Dodaje idempotentne zadanie Google Sheets do outboxu PostgreSQL."""
    resolved_key = idempotency_key or str(uuid4())
    if idempotency_key:
        existing = (
            await session.execute(
                select(DeviceSheetOutbox).where(DeviceSheetOutbox.idempotency_key == resolved_key)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
    queue_item = DeviceSheetOutbox(
        unit_id=unit.id,
        idempotency_key=resolved_key,
        operation_type=operation_type,
        status="pending",
        payload=payload,
        attempt_count=0,
        max_attempts=10,
        next_attempt_at=datetime.now(UTC),
    )
    session.add(queue_item)
    unit.sheet_sync_status = "pending"
    unit.sheet_sync_error = None
    unit.updated_at = datetime.now(UTC)
    await session.flush()
    return queue_item


async def complete_intake_operation(
    session: AsyncSession,
    operation: DeviceIntakeOperation,
    result: dict[str, Any],
) -> list[DeviceInventoryUnit]:
    """Finalizuje rejestr po zatwierdzeniu transakcji Firebird."""
    operation.status = "completed"
    operation.firebird_pz_id = int(result["pz_id"])
    operation.firebird_pz_number = str(result["pz_number"])
    operation.result_snapshot = result
    operation.error_text = None
    operation.updated_at = datetime.now(UTC)
    operation.completed_at = datetime.now(UTC)

    created_units: list[DeviceInventoryUnit] = []
    for item in result.get("items", []):
        source_row = int(item["warehouse_item_id"])
        existing_stmt = select(DeviceInventoryUnit).where(
            DeviceInventoryUnit.source_type == DEVICE_SOURCE_TYPE,
            DeviceInventoryUnit.source_row == source_row,
        )
        unit = (await session.execute(existing_stmt)).scalar_one_or_none()
        if unit is None:
            serial = str(item["serial"]).strip()
            ewidencja = str(item["ewidencja"]).strip()
            unit = DeviceInventoryUnit(
                operation_id=operation.id,
                source_type=DEVICE_SOURCE_TYPE,
                source_row=source_row,
                firebird_pz_id=int(result["pz_id"]),
                firebird_zakpozycja_id=int(item["zakpozycja_id"]),
                firebird_machine_id=(
                    int(item["machine_id"]) if item.get("machine_id") is not None else None
                ),
                firebird_machine_table_id=(
                    int(item["machine_table_id"])
                    if item.get("machine_table_id") is not None
                    else None
                ),
                firebird_model_id=int(item["model_id"]),
                firebird_supplier_id=int(result["supplier_id"]),
                serial=serial,
                serial_normalized=normalize_device_identity(serial),
                ewidencja=ewidencja,
                ewidencja_normalized=normalize_device_identity(ewidencja),
                purchase_price_net=Decimal(str(item["purchase_price_netto"])),
                sheet_sync_status="pending",
                snapshot=item,
            )
            session.add(unit)
            await session.flush()

        sheet_payload = {
            "source_type": DEVICE_SOURCE_TYPE,
            "source_row": unit.source_row,
            "producer": item.get("producer"),
            "model": item.get("model"),
            "serial": unit.serial,
            "ewidencja": unit.ewidencja,
            "price": str(unit.purchase_price_net or ""),
            "status": "01. Przed zerówką",
            "reservation_status": "brak rezerwacji",
            "reservation_grenke": "",
            "reservation_until": "",
            "notes": AUTOMATED_PZ_NOTE,
            "notes_red": True,
            "ms_id_maszyna": unit.firebird_machine_id,
            "ctip_env": settings.ctip_runtime_profile.upper(),
        }
        await enqueue_sheet_operation(
            session,
            unit=unit,
            operation_type="upsert_device",
            payload=sheet_payload,
            idempotency_key=f"{operation.idempotency_key}:{unit.source_row}:sheet",
        )
        session.add(
            DeviceInventoryEvent(
                unit_id=unit.id,
                event_type="intake_created",
                created_by=operation.created_by,
                payload={
                    "operation_id": operation.id,
                    "firebird_pz_id": operation.firebird_pz_id,
                    "firebird_pz_number": operation.firebird_pz_number,
                },
            )
        )
        created_units.append(unit)

    await session.flush()
    return created_units


async def get_inventory_unit(
    session: AsyncSession,
    unit_id: int,
) -> DeviceInventoryUnit | None:
    """Zwraca pojedynczy egzemplarz rejestru CTIP."""
    return await session.get(DeviceInventoryUnit, unit_id)


async def ensure_inventory_unit_for_legacy(
    session: AsyncSession,
    *,
    source_row: int,
    snapshot: dict[str, Any],
) -> DeviceInventoryUnit:
    """Tworzy techniczne powiązanie dopiero przy jawnej akcji na starszym urządzeniu."""
    stmt = select(DeviceInventoryUnit).where(
        DeviceInventoryUnit.source_type == DEVICE_SOURCE_TYPE,
        DeviceInventoryUnit.source_row == source_row,
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        return existing

    serial = str(snapshot.get("serial") or "").strip()
    ewidencja = str(snapshot.get("ewidencja") or snapshot.get("index") or "").strip()
    if not serial or not ewidencja:
        raise ValueError("Urządzenie historyczne nie ma kompletnego serialu i numeru KP.")
    unit = DeviceInventoryUnit(
        operation_id=None,
        source_type=DEVICE_SOURCE_TYPE,
        source_row=source_row,
        firebird_pz_id=snapshot.get("pz_id"),
        firebird_zakpozycja_id=snapshot.get("zakpozycja_id"),
        firebird_machine_id=snapshot.get("machine_id"),
        firebird_machine_table_id=snapshot.get("machine_table_id"),
        firebird_model_id=snapshot.get("model_id"),
        firebird_supplier_id=snapshot.get("supplier_id"),
        serial=serial,
        serial_normalized=normalize_device_identity(serial),
        ewidencja=ewidencja,
        ewidencja_normalized=normalize_device_identity(ewidencja),
        purchase_price_net=(
            Decimal(str(snapshot["purchase_price_net"]))
            if snapshot.get("purchase_price_net") not in (None, "")
            else None
        ),
        sheet_row=snapshot.get("sheet_row"),
        sheet_sync_status="synced" if snapshot.get("sheet_row") else "pending",
        snapshot=snapshot,
    )
    session.add(unit)
    await session.flush()
    return unit


async def add_device_note(
    session: AsyncSession,
    *,
    unit: DeviceInventoryUnit,
    user_id: int,
    note: str,
) -> DeviceInventoryEvent:
    """Dodaje wersjonowaną uwagę i kolejkuje jej publikację w arkuszu."""
    normalized = str(note or "").strip()
    if len(normalized) < 3:
        raise ValueError("Uwaga musi zawierać co najmniej 3 znaki.")
    event = DeviceInventoryEvent(
        unit_id=unit.id,
        event_type="note_changed",
        created_by=user_id,
        payload={"note": normalized},
    )
    session.add(event)
    await enqueue_sheet_operation(
        session,
        unit=unit,
        operation_type="update_note",
        payload={"notes": normalized},
    )
    await session.flush()
    return event


async def find_active_flow_reservation(
    session: AsyncSession,
    *,
    source_row: int,
) -> dict[str, Any] | None:
    """Sprawdza, czy egzemplarz jest zajęty przez aktywną sprawę FLOW."""
    stmt = (
        select(FormWorkflowDevice, FormWorkflowCase, FormRequest)
        .join(FormWorkflowCase, FormWorkflowCase.id == FormWorkflowDevice.workflow_case_id)
        .join(FormRequest, FormRequest.id == FormWorkflowCase.form_request_id)
        .where(
            FormWorkflowDevice.source_type == DEVICE_SOURCE_TYPE,
            FormWorkflowDevice.source_row == source_row,
            FormRequest.status == ACTIVE_FLOW_FORM_STATUS,
            FormWorkflowCase.resources_released_at.is_(None),
        )
        .order_by(FormWorkflowCase.updated_at.desc())
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        return None
    device, workflow_case, form_request = row
    return {
        "workflow_device_id": device.id,
        "workflow_case_id": workflow_case.id,
        "form_request_id": form_request.id,
        "business_status": workflow_case.business_status,
    }


async def get_active_manual_reservation(
    session: AsyncSession,
    *,
    unit_id: int,
) -> DeviceManualReservation | None:
    """Zwraca aktywną rezerwację ręczną egzemplarza."""
    stmt = select(DeviceManualReservation).where(
        DeviceManualReservation.unit_id == unit_id,
        DeviceManualReservation.released_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def save_manual_reservation(
    session: AsyncSession,
    *,
    unit: DeviceInventoryUnit,
    user_id: int,
    reserved_for: str,
    reason: str,
    expires_at: datetime,
) -> DeviceManualReservation:
    """Tworzy lub aktualizuje terminową rezerwację ręczną."""
    flow_reservation = await find_active_flow_reservation(session, source_row=unit.source_row)
    if flow_reservation is not None:
        raise DeviceReservationConflict(
            "Urządzenie jest zarezerwowane przez aktywną sprawę FLOW "
            f"nr {flow_reservation['form_request_id']}."
        )
    reserved_for_value = str(reserved_for or "").strip()
    reason_value = str(reason or "").strip()
    if len(reserved_for_value) < 2:
        raise ValueError("Podaj klienta lub cel rezerwacji.")
    if len(reason_value) < 10:
        raise ValueError("Uzasadnienie rezerwacji musi mieć co najmniej 10 znaków.")
    now = datetime.now(UTC)
    if expires_at <= now:
        raise ValueError("Termin rezerwacji musi przypadać w przyszłości.")

    reservation = await get_active_manual_reservation(session, unit_id=unit.id)
    event_type = "reservation_updated"
    if reservation is None:
        reservation = DeviceManualReservation(
            unit_id=unit.id,
            reserved_for=reserved_for_value,
            reason=reason_value,
            expires_at=expires_at,
            created_by=user_id,
        )
        session.add(reservation)
        event_type = "reservation_created"
    else:
        reservation.reserved_for = reserved_for_value
        reservation.reason = reason_value
        reservation.expires_at = expires_at
        reservation.updated_at = now

    session.add(
        DeviceInventoryEvent(
            unit_id=unit.id,
            event_type=event_type,
            created_by=user_id,
            payload={
                "reserved_for": reserved_for_value,
                "reason": reason_value,
                "expires_at": expires_at.isoformat(),
            },
        )
    )
    await enqueue_sheet_operation(
        session,
        unit=unit,
        operation_type="update_reservation",
        payload={
            "reservation_status": "03. Rezerwacja ręczna",
            "reservation_grenke": reserved_for_value,
            "reservation_until": expires_at.date().isoformat(),
        },
    )
    await session.flush()
    return reservation


async def release_manual_reservation(
    session: AsyncSession,
    *,
    unit: DeviceInventoryUnit,
    user_id: int | None,
    reason: str,
    event_type: str = "reservation_released",
) -> DeviceManualReservation:
    """Zwalnia aktywną rezerwację ręczną i kolejkuje aktualizację arkusza."""
    reason_value = str(reason or "").strip()
    if len(reason_value) < 10:
        raise ValueError("Uzasadnienie zwolnienia musi mieć co najmniej 10 znaków.")
    reservation = await get_active_manual_reservation(session, unit_id=unit.id)
    if reservation is None:
        raise ValueError("Urządzenie nie ma aktywnej rezerwacji ręcznej.")
    now = datetime.now(UTC)
    reservation.released_at = now
    reservation.released_by = user_id
    reservation.release_reason = reason_value
    reservation.updated_at = now
    session.add(
        DeviceInventoryEvent(
            unit_id=unit.id,
            event_type=event_type,
            created_by=user_id,
            payload={"reason": reason_value, "released_at": now.isoformat()},
        )
    )
    await enqueue_sheet_operation(
        session,
        unit=unit,
        operation_type="release_reservation",
        payload={
            "reservation_status": "brak rezerwacji",
            "reservation_grenke": "",
            "reservation_until": "",
        },
    )
    await session.flush()
    return reservation


async def list_device_events(
    session: AsyncSession,
    *,
    unit_id: int,
    limit: int = 100,
) -> list[DeviceInventoryEvent]:
    """Zwraca najnowszą historię operacyjną urządzenia."""
    stmt = (
        select(DeviceInventoryEvent)
        .where(DeviceInventoryEvent.unit_id == unit_id)
        .order_by(DeviceInventoryEvent.created_at.desc(), DeviceInventoryEvent.id.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


async def find_unit_by_source_or_identity(
    session: AsyncSession,
    *,
    source_row: int | None = None,
    serial: str | None = None,
    ewidencja: str | None = None,
) -> DeviceInventoryUnit | None:
    """Wyszukuje rejestr po źródle, serialu albo numerze KP."""
    conditions = []
    if source_row is not None:
        conditions.append(
            (DeviceInventoryUnit.source_type == DEVICE_SOURCE_TYPE)
            & (DeviceInventoryUnit.source_row == source_row)
        )
    serial_key = normalize_device_identity(serial)
    if serial_key:
        conditions.append(DeviceInventoryUnit.serial_normalized == serial_key)
    ewidencja_key = normalize_device_identity(ewidencja)
    if ewidencja_key:
        conditions.append(DeviceInventoryUnit.ewidencja_normalized == ewidencja_key)
    if not conditions:
        return None
    stmt = (
        select(DeviceInventoryUnit).where(or_(*conditions)).order_by(DeviceInventoryUnit.id.asc())
    )
    return (await session.execute(stmt)).scalars().first()


__all__ = [
    "AUTOMATED_PZ_NOTE",
    "DEVICE_SOURCE_TYPE",
    "DeviceIdempotencyConflict",
    "DeviceReservationConflict",
    "add_device_note",
    "begin_intake_operation",
    "canonical_request_hash",
    "complete_intake_operation",
    "enqueue_sheet_operation",
    "ensure_inventory_unit_for_legacy",
    "find_active_flow_reservation",
    "find_intake_operation",
    "find_unit_by_source_or_identity",
    "get_active_manual_reservation",
    "get_inventory_unit",
    "intake_operation_payload",
    "list_device_events",
    "mark_intake_operation_failed",
    "normalize_device_identity",
    "release_manual_reservation",
    "save_manual_reservation",
]
