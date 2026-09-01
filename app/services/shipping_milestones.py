"""Uzgadnianie kamieni milowych DPD z polami zlecenia Menadżera Serwisu."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models import ShippingEvent, ShippingShipment, ShippingTrackingEvent
from app.services.dpd_infoservices_sync import (
    classify_dpd_event,
    is_dpd_pickup_confirmation,
)
from app.services.shipping_firebird import (
    ShippingOrderStateConflict,
    write_shipping_milestones_to_order,
)

WARSAW = ZoneInfo("Europe/Warsaw")
DESCRIPTION_CATEGORIES = {"delivered", "undelivered", "redirected", "returning", "critical"}


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
        event for event in events if event.operation_type == "INSERT" and not event.is_cancelled
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


async def _reconcile_shipment(
    session: AsyncSession,
    *,
    shipment: ShippingShipment,
    events: list[ShippingTrackingEvent],
) -> tuple[bool, bool]:
    """Uzgadnia jeden numer listu z jednym powiązanym zleceniem MS."""
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

    pickup_to_write = (
        pickup if pickup and pickup.source_event_key != shipment.firebird_pickup_event_key else None
    )
    delivery_to_write = (
        delivery
        if delivery and delivery.source_event_key != shipment.firebird_delivery_event_key
        else None
    )
    description_to_write = (
        description
        if description and description.source_event_key != shipment.firebird_description_event_key
        else None
    )
    if not any((pickup_to_write, delivery_to_write, description_to_write)):
        shipment.firebird_milestone_error = None
        return False, False

    previous_description_event = stored_description_event
    pickup_time = _local_event_time(pickup_to_write) if pickup_to_write else None
    delivery_time = _local_event_time(delivery_to_write) if delivery_to_write else None
    description_text = (
        shipping_milestone_description(description_to_write) if description_to_write else None
    )
    expected_description = (
        shipping_milestone_description(previous_description_event)
        if previous_description_event
        else None
    )
    tracking_number = str(shipment.tracking_number or "").strip()
    result = await asyncio.to_thread(
        write_shipping_milestones_to_order,
        order_table_id=shipment.shipping_case.firebird_order_table_id,
        tracking_number=tracking_number,
        pickup_date=pickup_time.date() if pickup_time else None,
        pickup_note=(
            f"Wysłana paczka {pickup_time.strftime('%d.%m.%Y')} {tracking_number}"
            if pickup_time
            else None
        ),
        delivery_date=delivery_time.date() if delivery_time else None,
        description_text=description_text,
        expected_description_text=expected_description,
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
    session.add(
        ShippingEvent(
            shipping_case_id=shipment.shipping_case_id,
            shipment_id=shipment.id,
            event_type="dpd_firebird_milestones_synchronized",
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


__all__ = [
    "reconcile_shipping_milestones",
    "shipping_milestone_description",
]
