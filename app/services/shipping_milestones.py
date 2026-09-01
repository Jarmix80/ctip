"""Uzgadnianie kamieni milowych DPD z polami zlecenia Menadżera Serwisu."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models import ShippingCase, ShippingEvent, ShippingShipment, ShippingTrackingEvent
from app.services.dpd_infoservices_sync import (
    classify_dpd_event,
    is_dpd_pickup_confirmation,
)
from app.services.shipping_firebird import (
    ShippingOrderStateConflict,
    preview_shipping_milestones_to_order,
    restore_shipping_milestones_to_order,
    write_shipping_milestones_to_order,
)

WARSAW = ZoneInfo("Europe/Warsaw")
DESCRIPTION_CATEGORIES = {"delivered", "undelivered", "redirected", "returning", "critical"}
PILOT_APPLIED_EVENT = "dpd_firebird_milestones_pilot_applied"
PILOT_ROLLED_BACK_EVENT = "dpd_firebird_milestones_pilot_rolled_back"


class ShippingMilestonePilotValidationError(RuntimeError):
    """Oznacza niespełnienie warunków pojedynczego pilota archiwalnego."""


def _event_timestamp(event: ShippingTrackingEvent) -> float:
    """Buduje stabilny klucz sortowania zdarzeń także przy braku czasu DPD."""
    if event.event_time is None:
        return float("-inf")
    value = event.event_time
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.timestamp()


def _local_event_time(event: ShippingTrackingEvent) -> datetime | None:
    """Przelicza czas zdarzenia DPD na polską strefę czasową."""
    if event.event_time is None:
        return None
    value = event.event_time
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(WARSAW)


def shipping_milestone_description(event: ShippingTrackingEvent) -> str:
    """Formatuje ostatni istotny status do pola `PRZESYLKA_WE`."""
    classification = classify_dpd_event(event.business_code, event.description)
    local_time = _local_event_time(event)
    timestamp = local_time.strftime("%d.%m.%Y %H:%M") if local_time else "DPD"
    description = str(event.description or classification["label"]).strip()
    code = str(event.business_code or "").strip()
    suffix = f" [kod {code}]" if code else ""
    return f"{timestamp} — {classification['label']}: {description}{suffix}"[:250]


def _active_events(events: list[ShippingTrackingEvent]) -> list[ShippingTrackingEvent]:
    return [
        event
        for event in events
        if event.operation_type == "INSERT"
        and event.canonical_event_id is None
        and not event.is_cancelled
    ]


def _pickup_event(events: list[ShippingTrackingEvent]) -> ShippingTrackingEvent | None:
    eligible = [
        event
        for event in _active_events(events)
        if event.event_time is not None
        and is_dpd_pickup_confirmation(event.business_code, event.description)
    ]
    return (
        min(eligible, key=lambda event: (_event_timestamp(event), event.id)) if eligible else None
    )


def _delivery_event(events: list[ShippingTrackingEvent]) -> ShippingTrackingEvent | None:
    eligible = [
        event
        for event in _active_events(events)
        if event.event_time is not None
        and classify_dpd_event(event.business_code, event.description)["category"] == "delivered"
    ]
    return (
        min(eligible, key=lambda event: (_event_timestamp(event), event.id)) if eligible else None
    )


def _description_event(events: list[ShippingTrackingEvent]) -> ShippingTrackingEvent | None:
    eligible = [
        event
        for event in _active_events(events)
        if classify_dpd_event(event.business_code, event.description)["category"]
        in DESCRIPTION_CATEGORIES
    ]
    return (
        max(eligible, key=lambda event: (_event_timestamp(event), event.id)) if eligible else None
    )


def _event_by_key(
    events: list[ShippingTrackingEvent],
    source_event_key: str | None,
) -> ShippingTrackingEvent | None:
    if not source_event_key:
        return None
    return next(
        (event for event in events if event.source_event_key == source_event_key),
        None,
    )


def _milestone_conflict(
    *,
    stored_key: str | None,
    current_event: ShippingTrackingEvent | None,
    label: str,
) -> None:
    """Blokuje automatyczne cofanie dat po anulowaniu lub zmianie zdarzenia DPD."""
    if stored_key and (current_event is None or current_event.source_event_key != stored_key):
        raise ShippingOrderStateConflict(
            f"Zdarzenie DPD zapisane wcześniej jako {label} zostało anulowane albo zastąpione. "
            "Dane MS wymagają ręcznego uzgodnienia."
        )


def _shipment_milestone_events(
    shipment: ShippingShipment,
    events: list[ShippingTrackingEvent],
) -> dict[str, ShippingTrackingEvent | None]:
    """Wybiera nowe zdarzenia do zapisu i kontroluje wcześniejsze powiązania."""
    pickup = _pickup_event(events)
    delivery = _delivery_event(events)
    description = _description_event(events)
    _milestone_conflict(
        stored_key=shipment.firebird_pickup_event_key,
        current_event=pickup,
        label="nadanie",
    )
    _milestone_conflict(
        stored_key=shipment.firebird_delivery_event_key,
        current_event=delivery,
        label="doręczenie",
    )
    stored_description_event = _event_by_key(events, shipment.firebird_description_event_key)
    if shipment.firebird_description_event_key and (
        stored_description_event is None or stored_description_event.is_cancelled
    ):
        raise ShippingOrderStateConflict(
            "Zdarzenie DPD zapisane wcześniej jako opis przesyłki zostało anulowane. "
            "Dane MS wymagają ręcznego uzgodnienia."
        )
    return {
        "pickup": (
            pickup
            if pickup and pickup.source_event_key != shipment.firebird_pickup_event_key
            else None
        ),
        "delivery": (
            delivery
            if delivery and delivery.source_event_key != shipment.firebird_delivery_event_key
            else None
        ),
        "description": (
            description
            if description
            and description.source_event_key != shipment.firebird_description_event_key
            else None
        ),
        "previous_description": stored_description_event,
    }


def _milestone_write_arguments(
    shipment: ShippingShipment,
    selected: dict[str, ShippingTrackingEvent | None],
) -> dict[str, Any]:
    """Buduje wspólne argumenty podglądu i zapisu pól przesyłki MS."""
    pickup = selected["pickup"]
    delivery = selected["delivery"]
    description = selected["description"]
    previous_description = selected["previous_description"]
    pickup_time = _local_event_time(pickup) if pickup else None
    delivery_time = _local_event_time(delivery) if delivery else None
    tracking_number = str(shipment.tracking_number or "").strip()
    return {
        "order_table_id": shipment.shipping_case.firebird_order_table_id,
        "tracking_number": tracking_number,
        "pickup_date": pickup_time.date() if pickup_time else None,
        "pickup_note": (
            f"Wysłana paczka {pickup_time.strftime('%d.%m.%Y')} {tracking_number}"
            if pickup_time
            else None
        ),
        "delivery_date": delivery_time.date() if delivery_time else None,
        "description_text": (shipping_milestone_description(description) if description else None),
        "expected_description_text": (
            shipping_milestone_description(previous_description) if previous_description else None
        ),
    }


async def _reconcile_shipment(
    session: AsyncSession,
    *,
    shipment: ShippingShipment,
    events: list[ShippingTrackingEvent],
    expected_state_token: str | None = None,
    pilot_audit: dict[str, Any] | None = None,
) -> tuple[bool, bool]:
    """Uzgadnia jeden numer listu z jednym powiązanym zleceniem MS."""
    selected = _shipment_milestone_events(shipment, events)
    pickup_to_write = selected["pickup"]
    delivery_to_write = selected["delivery"]
    description_to_write = selected["description"]
    if not any((pickup_to_write, delivery_to_write, description_to_write)):
        shipment.firebird_milestone_error = None
        return False, False

    writer_arguments = _milestone_write_arguments(shipment, selected)
    tracking_number = str(writer_arguments["tracking_number"])
    result = await asyncio.to_thread(
        write_shipping_milestones_to_order,
        **writer_arguments,
        expected_state_token=expected_state_token,
    )
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "tracking_number": tracking_number,
        "changed_fields": result["changed_fields"],
    }
    if pickup_to_write:
        shipment.firebird_pickup_event_key = pickup_to_write.source_event_key
        shipment.firebird_pickup_synced_at = now
        payload["pickup_event_key"] = pickup_to_write.source_event_key
    if delivery_to_write:
        shipment.firebird_delivery_event_key = delivery_to_write.source_event_key
        shipment.firebird_delivery_synced_at = now
        payload["delivery_event_key"] = delivery_to_write.source_event_key
    if description_to_write:
        shipment.firebird_description_event_key = description_to_write.source_event_key
        shipment.firebird_description_synced_at = now
        payload["description_event_key"] = description_to_write.source_event_key
    shipment.firebird_milestone_error = None
    shipment.updated_at = now
    if pilot_audit:
        payload.update(pilot_audit)
    session.add(
        ShippingEvent(
            shipping_case_id=shipment.shipping_case_id,
            shipment_id=shipment.id,
            event_type=(
                PILOT_APPLIED_EVENT if pilot_audit else "dpd_firebird_milestones_synchronized"
            ),
            payload=payload,
            created_by=None,
            created_at=now,
        )
    )
    return True, False


async def reconcile_shipping_milestones(session: AsyncSession) -> dict[str, int]:
    """Ponawia zapisy DPD dla wszystkich nowych przesyłek zarządzanych przez CTIP."""
    result = {"eligible": 0, "written": 0, "failed": 0, "conflicts": 0}
    if not settings.shipping_dpd_firebird_milestones_enabled:
        return result
    shipments = list(
        (
            await session.execute(
                select(ShippingShipment)
                .options(selectinload(ShippingShipment.shipping_case))
                .where(
                    ShippingShipment.provider == "dpd",
                    ShippingShipment.tracking_number.is_not(None),
                    ShippingShipment.firebird_status == "written",
                    ShippingShipment.firebird_label_metadata_synced_at.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if not shipments:
        return result
    waybills = {str(shipment.tracking_number) for shipment in shipments}
    events = list(
        (
            await session.execute(
                select(ShippingTrackingEvent)
                .where(ShippingTrackingEvent.waybill.in_(waybills))
                .order_by(ShippingTrackingEvent.id.asc())
            )
        )
        .scalars()
        .all()
    )
    events_by_waybill: dict[str, list[ShippingTrackingEvent]] = defaultdict(list)
    for event in events:
        if event.waybill:
            events_by_waybill[str(event.waybill)].append(event)

    for shipment in shipments:
        shipment_events = events_by_waybill.get(str(shipment.tracking_number), [])
        if not shipment_events:
            continue
        result["eligible"] += 1
        previous_error = shipment.firebird_milestone_error
        try:
            written, _ = await _reconcile_shipment(
                session,
                shipment=shipment,
                events=shipment_events,
            )
            if written:
                result["written"] += 1
        except ShippingOrderStateConflict as exc:
            result["conflicts"] += 1
            shipment.firebird_milestone_error = str(exc)[:2000]
        except Exception as exc:
            result["failed"] += 1
            shipment.firebird_milestone_error = str(exc)[:2000]
        if (
            shipment.firebird_milestone_error
            and shipment.firebird_milestone_error != previous_error
        ):
            now = datetime.now(UTC)
            shipment.updated_at = now
            session.add(
                ShippingEvent(
                    shipping_case_id=shipment.shipping_case_id,
                    shipment_id=shipment.id,
                    event_type="dpd_firebird_milestones_failed",
                    payload={
                        "tracking_number": shipment.tracking_number,
                        "error": shipment.firebird_milestone_error,
                    },
                    created_by=None,
                    created_at=now,
                )
            )
    return result


def _pilot_event_payload(event: ShippingTrackingEvent | None) -> dict[str, Any] | None:
    if event is None:
        return None
    classification = classify_dpd_event(event.business_code, event.description)
    local_time = _local_event_time(event)
    return {
        "source_event_key": event.source_event_key,
        "business_code": event.business_code,
        "description": event.description,
        "event_time": local_time.isoformat() if local_time else None,
        "category": classification["category"],
        "label": classification["label"],
        "is_cancelled": bool(event.is_cancelled),
    }


def _pilot_postgres_state(shipment: ShippingShipment) -> dict[str, Any]:
    return {
        "pickup_event_key": shipment.firebird_pickup_event_key,
        "pickup_synced_at": (
            shipment.firebird_pickup_synced_at.isoformat()
            if shipment.firebird_pickup_synced_at
            else None
        ),
        "delivery_event_key": shipment.firebird_delivery_event_key,
        "delivery_synced_at": (
            shipment.firebird_delivery_synced_at.isoformat()
            if shipment.firebird_delivery_synced_at
            else None
        ),
        "description_event_key": shipment.firebird_description_event_key,
        "description_synced_at": (
            shipment.firebird_description_synced_at.isoformat()
            if shipment.firebird_description_synced_at
            else None
        ),
        "milestone_error": shipment.firebird_milestone_error,
    }


def _pilot_token(payload: dict[str, Any]) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _validate_archived_pilot_shipment(shipment: ShippingShipment) -> None:
    if settings.shipping_dpd_firebird_milestones_enabled:
        raise ShippingMilestonePilotValidationError(
            "Globalna synchronizacja pól MS musi pozostać wyłączona podczas pilota."
        )
    if shipment.provider != "dpd" or shipment.provider_mode != "production":
        raise ShippingMilestonePilotValidationError(
            "Pilot wymaga rzeczywistej produkcyjnej przesyłki DPD."
        )
    if (
        shipment.status != "closed"
        or shipment.shipping_case.status != "closed"
        or shipment.closed_at is None
    ):
        raise ShippingMilestonePilotValidationError(
            "Pilot może objąć wyłącznie zakończone zlecenie z Archiwum."
        )
    if shipment.firebird_status != "written":
        raise ShippingMilestonePilotValidationError(
            "Zlecenie nie ma potwierdzonego wcześniejszego zapisu procesu Shipping w MS."
        )
    if shipment.firebird_label_metadata_synced_at is not None:
        raise ShippingMilestonePilotValidationError(
            "Przesyłka należy już do automatycznej synchronizacji i nie jest pilotem historycznym."
        )


async def _load_archived_pilot_target(
    session: AsyncSession,
    *,
    order_id: int,
    order_year: int,
    waybill: str,
    for_update: bool,
) -> tuple[ShippingShipment, list[ShippingTrackingEvent]]:
    statement = (
        select(ShippingShipment)
        .join(ShippingCase, ShippingCase.id == ShippingShipment.shipping_case_id)
        .options(selectinload(ShippingShipment.shipping_case))
        .where(
            ShippingCase.firebird_order_id == int(order_id),
            ShippingCase.firebird_order_year == int(order_year),
            ShippingShipment.tracking_number == str(waybill).strip(),
        )
    )
    if for_update:
        statement = statement.with_for_update()
    shipments = list((await session.execute(statement)).scalars().unique().all())
    if len(shipments) != 1:
        raise ShippingMilestonePilotValidationError(
            "Nie znaleziono dokładnie jednej przesyłki dla wskazanego zlecenia i listu."
        )
    shipment = shipments[0]
    _validate_archived_pilot_shipment(shipment)
    events = list(
        (
            await session.execute(
                select(ShippingTrackingEvent)
                .where(ShippingTrackingEvent.waybill == str(waybill).strip())
                .order_by(ShippingTrackingEvent.id.asc())
            )
        )
        .scalars()
        .all()
    )
    if not events:
        raise ShippingMilestonePilotValidationError(
            "Brak zdarzeń InfoServices dla wskazanego numeru listu."
        )
    return shipment, events


async def _build_archived_pilot_preview(
    shipment: ShippingShipment,
    events: list[ShippingTrackingEvent],
) -> dict[str, Any]:
    selected = _shipment_milestone_events(shipment, events)
    if selected["pickup"] is None or selected["delivery"] is None:
        raise ShippingMilestonePilotValidationError(
            "Pilot archiwalny wymaga potwierdzonego odbioru przez kuriera i doręczenia."
        )
    writer_arguments = _milestone_write_arguments(shipment, selected)
    firebird_preview = await asyncio.to_thread(
        preview_shipping_milestones_to_order,
        **writer_arguments,
    )
    order = firebird_preview["order"]
    case = shipment.shipping_case
    if (
        int(order["order_table_id"]) != int(case.firebird_order_table_id)
        or int(order["order_id"]) != int(case.firebird_order_id)
        or int(order["order_year"]) != int(case.firebird_order_year)
    ):
        raise ShippingMilestonePilotValidationError(
            "Tożsamość zlecenia w Firebird nie zgadza się z rekordem Shipping."
        )
    if order["status"] != "Z":
        raise ShippingMilestonePilotValidationError(
            "Archiwalne zlecenie nie ma w MS oczekiwanego stanu zrealizowanego Z."
        )
    token_payload = {
        "shipment_id": shipment.id,
        "shipping_case_id": shipment.shipping_case_id,
        "order": order,
        "waybill": shipment.tracking_number,
        "postgres": _pilot_postgres_state(shipment),
        "events": {
            "pickup": _pilot_event_payload(selected["pickup"]),
            "delivery": _pilot_event_payload(selected["delivery"]),
            "description": _pilot_event_payload(selected["description"]),
        },
        "firebird": firebird_preview,
    }
    return {
        "mode": "dry-run",
        "eligible": True,
        "historical": True,
        "automatic_eligible": False,
        "shipment_id": shipment.id,
        "shipping_case_id": shipment.shipping_case_id,
        "order": f"{case.firebird_order_id}/{case.firebird_order_year}",
        "order_table_id": case.firebird_order_table_id,
        "waybill": shipment.tracking_number,
        "events": token_payload["events"],
        "firebird": firebird_preview,
        "state_token": _pilot_token(token_payload),
    }


async def preview_archived_shipping_milestone_pilot(
    session: AsyncSession,
    *,
    order_id: int,
    order_year: int,
    waybill: str,
) -> dict[str, Any]:
    """Pokazuje dokładny zakres pojedynczego pilota bez wykonywania zapisu."""
    shipment, events = await _load_archived_pilot_target(
        session,
        order_id=order_id,
        order_year=order_year,
        waybill=waybill,
        for_update=False,
    )
    return await _build_archived_pilot_preview(shipment, events)


async def apply_archived_shipping_milestone_pilot(
    session: AsyncSession,
    *,
    order_id: int,
    order_year: int,
    waybill: str,
    expected_state_token: str,
) -> dict[str, Any]:
    """Zapisuje kamienie milowe dokładnie jednego zatwierdzonego dry-runu."""
    shipment, events = await _load_archived_pilot_target(
        session,
        order_id=order_id,
        order_year=order_year,
        waybill=waybill,
        for_update=True,
    )
    preview = await _build_archived_pilot_preview(shipment, events)
    if preview["state_token"] != str(expected_state_token).strip():
        raise ShippingMilestonePilotValidationError(
            "Stan przesyłki albo zlecenia zmienił się od dry-run. Ponów podgląd."
        )
    pilot_run_id = uuid.uuid4().hex
    previous_postgres = _pilot_postgres_state(shipment)
    selected = _shipment_milestone_events(shipment, events)
    expected_postgres = {
        "pickup_event_key": selected["pickup"].source_event_key,
        "delivery_event_key": selected["delivery"].source_event_key,
        "description_event_key": (
            selected["description"].source_event_key if selected["description"] else None
        ),
    }
    written, _ = await _reconcile_shipment(
        session,
        shipment=shipment,
        events=events,
        expected_state_token=preview["firebird"]["state_token"],
        pilot_audit={
            "pilot_run_id": pilot_run_id,
            "pilot_order": preview["order"],
            "pilot_order_table_id": preview["order_table_id"],
            "pilot_before": preview["firebird"]["before"],
            "pilot_after": preview["firebird"]["after"],
            "pilot_planned_changed_fields": preview["firebird"]["changed_fields"],
            "pilot_postgres_before": previous_postgres,
            "pilot_postgres_after": expected_postgres,
            "pilot_state_token": preview["state_token"],
        },
    )
    if not written:
        raise ShippingMilestonePilotValidationError(
            "Brak nowych kamieni milowych do zapisania dla wskazanej przesyłki."
        )
    await session.flush()
    return {
        **preview,
        "mode": "apply",
        "status": "written",
        "pilot_run_id": pilot_run_id,
    }


def _datetime_from_payload(value: Any) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


async def rollback_archived_shipping_milestone_pilot(
    session: AsyncSession,
    *,
    order_id: int,
    order_year: int,
    waybill: str,
    pilot_run_id: str,
) -> dict[str, Any]:
    """Wycofuje pola i klucze zapisane przez jeden wskazany przebieg pilota."""
    shipment, _ = await _load_archived_pilot_target(
        session,
        order_id=order_id,
        order_year=order_year,
        waybill=waybill,
        for_update=True,
    )
    events = list(
        (
            await session.execute(
                select(ShippingEvent)
                .where(
                    ShippingEvent.shipment_id == shipment.id,
                    ShippingEvent.event_type.in_((PILOT_APPLIED_EVENT, PILOT_ROLLED_BACK_EVENT)),
                )
                .order_by(ShippingEvent.id.asc())
            )
        )
        .scalars()
        .all()
    )
    applied_event = next(
        (
            event
            for event in events
            if event.event_type == PILOT_APPLIED_EVENT
            and event.payload.get("pilot_run_id") == pilot_run_id
        ),
        None,
    )
    if applied_event is None:
        raise ShippingMilestonePilotValidationError(
            "Nie znaleziono audytu wskazanego przebiegu pilota."
        )
    if any(
        event.event_type == PILOT_ROLLED_BACK_EVENT
        and event.payload.get("pilot_run_id") == pilot_run_id
        for event in events
    ):
        raise ShippingMilestonePilotValidationError("Ten przebieg pilota został już wycofany.")

    payload = applied_event.payload
    expected_postgres = payload["pilot_postgres_after"]
    current_postgres = _pilot_postgres_state(shipment)
    for key, expected_value in expected_postgres.items():
        if current_postgres[key] != expected_value:
            raise ShippingMilestonePilotValidationError(
                "Klucze uzgodnienia zmieniły się po pilocie; automatyczny rollback jest zabroniony."
            )
    changed_fields = list(payload.get("changed_fields") or [])
    restore_result = await asyncio.to_thread(
        restore_shipping_milestones_to_order,
        order_table_id=shipment.shipping_case.firebird_order_table_id,
        tracking_number=str(shipment.tracking_number),
        changed_fields=changed_fields,
        before=dict(payload["pilot_before"]),
        expected_after=dict(payload["pilot_after"]),
    )
    previous = payload["pilot_postgres_before"]
    shipment.firebird_pickup_event_key = previous["pickup_event_key"]
    shipment.firebird_pickup_synced_at = _datetime_from_payload(previous["pickup_synced_at"])
    shipment.firebird_delivery_event_key = previous["delivery_event_key"]
    shipment.firebird_delivery_synced_at = _datetime_from_payload(previous["delivery_synced_at"])
    shipment.firebird_description_event_key = previous["description_event_key"]
    shipment.firebird_description_synced_at = _datetime_from_payload(
        previous["description_synced_at"]
    )
    shipment.firebird_milestone_error = previous["milestone_error"]
    shipment.updated_at = datetime.now(UTC)
    session.add(
        ShippingEvent(
            shipping_case_id=shipment.shipping_case_id,
            shipment_id=shipment.id,
            event_type=PILOT_ROLLED_BACK_EVENT,
            payload={
                "pilot_run_id": pilot_run_id,
                "tracking_number": shipment.tracking_number,
                "changed_fields": restore_result["changed_fields"],
            },
            created_by=None,
            created_at=datetime.now(UTC),
        )
    )
    await session.flush()
    return {
        "mode": "rollback",
        "status": "restored",
        "pilot_run_id": pilot_run_id,
        "order": f"{order_id}/{order_year}",
        "waybill": waybill,
        "changed_fields": restore_result["changed_fields"],
    }


__all__ = [
    "ShippingMilestonePilotValidationError",
    "apply_archived_shipping_milestone_pilot",
    "preview_archived_shipping_milestone_pilot",
    "reconcile_shipping_milestones",
    "rollback_archived_shipping_milestone_pilot",
    "shipping_milestone_description",
]
