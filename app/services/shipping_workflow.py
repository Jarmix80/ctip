"""Orkiestracja przeglądu, nadania i zamknięcia przesyłek."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models import (
    ShippingAddress,
    ShippingCase,
    ShippingConsumableCompatibility,
    ShippingDayClose,
    ShippingEvent,
    ShippingItem,
    ShippingShipment,
)
from app.schemas.shipping import ShippingReviewRequest
from app.services.dpd_shipping import DpdShippingClient
from app.services.email_client import send_smtp_message
from app.services.firebird_runtime import firebird_writes_enabled
from app.services.shipping_firebird import (
    create_rw_and_close_order,
    load_toner_stock,
    write_shipment_to_order,
)
from app.services.sms_provider import HttpSmsProvider

ACTIVE_RESERVATION_STATUSES = ("ready", "shipment_created", "handed_over")


class ShippingConflictError(RuntimeError):
    """Konflikt stanu lub klucza idempotencji w procesie wysyłki."""


def _now() -> datetime:
    return datetime.now(UTC)


def _location_key(order: dict[str, Any], address: dict[str, Any]) -> str:
    raw = "|".join(
        str(value or "").strip().casefold()
        for value in (
            order.get("order_location") or order.get("machine_location"),
            address.get("street"),
            address.get("postal_code"),
            address.get("city"),
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _serialize_case(case: ShippingCase) -> dict[str, Any]:
    shipment = case.shipment
    return {
        "id": case.id,
        "order_table_id": case.firebird_order_table_id,
        "order_id": case.firebird_order_id,
        "order_year": case.firebird_order_year,
        "status": case.status,
        "order_kind": case.order_kind,
        "weight_kg": float(case.weight_kg),
        "address": case.address_snapshot,
        "items": [
            {
                "id": item.id,
                "firebird_warehouse_item_id": item.firebird_warehouse_item_id,
                "warehouse_id": item.warehouse_id,
                "item_index": item.item_index,
                "item_name": item.item_name,
                "unit": item.unit,
                "quantity": float(item.quantity),
                "firebird_position_id": item.firebird_position_id,
            }
            for item in case.items
        ],
        "shipment": (
            None
            if shipment is None
            else {
                "id": shipment.id,
                "status": shipment.status,
                "provider_mode": shipment.provider_mode,
                "tracking_number": shipment.tracking_number,
                "label_available": bool(shipment.label_content),
                "firebird_status": shipment.firebird_status,
                "firebird_error": shipment.firebird_error,
                "notification_sms_status": shipment.notification_sms_status,
                "notification_email_status": shipment.notification_email_status,
                "created_at": shipment.created_at.isoformat() if shipment.created_at else None,
                "handed_over_at": (
                    shipment.handed_over_at.isoformat() if shipment.handed_over_at else None
                ),
            }
        ),
    }


async def get_shipping_case(session: AsyncSession, order_table_id: int) -> ShippingCase | None:
    """Pobiera sprawę z pozycjami i przesyłką."""
    stmt = (
        select(ShippingCase)
        .options(selectinload(ShippingCase.items), selectinload(ShippingCase.shipment))
        .where(ShippingCase.firebird_order_table_id == int(order_table_id))
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def serialize_shipping_case(
    session: AsyncSession, order_table_id: int
) -> dict[str, Any] | None:
    """Zwraca serializowany stan sprawy dla API."""
    case = await get_shipping_case(session, order_table_id)
    return _serialize_case(case) if case else None


async def _soft_reservations(
    session: AsyncSession, *, exclude_case_id: int | None = None
) -> dict[int, Decimal]:
    stmt = (
        select(ShippingItem.firebird_warehouse_item_id, func.sum(ShippingItem.quantity))
        .join(ShippingCase, ShippingCase.id == ShippingItem.shipping_case_id)
        .where(ShippingCase.status.in_(ACTIVE_RESERVATION_STATUSES))
        .group_by(ShippingItem.firebird_warehouse_item_id)
    )
    if exclude_case_id is not None:
        stmt = stmt.where(ShippingCase.id != exclude_case_id)
    rows = (await session.execute(stmt)).all()
    return {int(item_id): Decimal(str(quantity or 0)) for item_id, quantity in rows}


async def build_stock_payload(
    session: AsyncSession,
    *,
    model_id: int | None,
    exclude_case_id: int | None = None,
) -> list[dict[str, Any]]:
    """Łączy fizyczny stan Firebird, miękkie rezerwacje i potwierdzoną zgodność."""
    stock = await asyncio.to_thread(load_toner_stock, warehouse_id=settings.shipping_warehouse_id)
    reservations = await _soft_reservations(session, exclude_case_id=exclude_case_id)
    compatible_ids: set[int] = set()
    if model_id:
        stmt = select(ShippingConsumableCompatibility.firebird_warehouse_item_id).where(
            ShippingConsumableCompatibility.firebird_model_id == int(model_id)
        )
        compatible_ids = {int(value) for value in (await session.execute(stmt)).scalars().all()}
    for item in stock:
        item_id = int(item["warehouse_item_id"])
        soft_reserved = reservations.get(item_id, Decimal("0"))
        item["soft_reserved_quantity"] = float(soft_reserved)
        item["available_after_soft_reservations"] = max(
            0.0, float(item["available_quantity"]) - float(soft_reserved)
        )
        item["compatible"] = item_id in compatible_ids
    stock.sort(key=lambda item: (not item["compatible"], item.get("item_name") or ""))
    return stock


async def review_shipping_order(
    session: AsyncSession,
    *,
    order: dict[str, Any],
    payload: ShippingReviewRequest,
    user_id: int,
) -> dict[str, Any]:
    """Weryfikuje dane, zapisuje adres i tworzy miękką rezerwację pozycji."""
    case = await get_shipping_case(session, int(order["order_table_id"]))
    if case and case.shipment is not None:
        raise ShippingConflictError(
            "Dla zlecenia istnieje już przesyłka; nie można zmienić wyboru."
        )
    now = _now()
    address_data = payload.address.model_dump(mode="json")
    location_key = _location_key(order, address_data)
    address_row = None
    if payload.save_address:
        stmt = select(ShippingAddress).where(
            ShippingAddress.firebird_client_id == int(order["client_id"]),
            ShippingAddress.location_key == location_key,
        )
        address_row = (await session.execute(stmt)).scalar_one_or_none()
        values = {
            "firebird_machine_id": order.get("machine_id"),
            "company_name": address_data["company_name"],
            "contact_name": address_data.get("contact_name"),
            "street": address_data["street"],
            "postal_code": address_data["postal_code"],
            "city": address_data["city"],
            "country_code": "PL",
            "phone": address_data["phone"],
            "email": address_data.get("email"),
            "source": address_data["source"],
            "verified_by": user_id,
            "verified_at": now,
            "updated_at": now,
        }
        if address_row is None:
            address_row = ShippingAddress(
                firebird_client_id=int(order["client_id"]),
                location_key=location_key,
                **values,
            )
            session.add(address_row)
            await session.flush()
        else:
            for key, value in values.items():
                setattr(address_row, key, value)

    if case is None:
        case = ShippingCase(
            firebird_order_table_id=int(order["order_table_id"]),
            firebird_order_id=int(order["order_id"]),
            firebird_order_year=int(order["order_year"]),
            firebird_client_id=int(order["client_id"]),
            firebird_machine_id=order.get("machine_id"),
            firebird_model_id=order.get("model_id"),
            order_kind=order.get("order_kind"),
            status="review_pending",
            address_snapshot={},
            source_snapshot=order,
            weight_kg=payload.weight_kg,
            created_at=now,
            updated_at=now,
            items=[],
        )
        session.add(case)
        await session.flush()

    stock = await build_stock_payload(
        session,
        model_id=order.get("model_id"),
        exclude_case_id=case.id,
    )
    stock_by_id = {int(item["warehouse_item_id"]): item for item in stock}
    seen: set[int] = set()
    selected: list[tuple[Any, dict[str, Any]]] = []
    for requested in payload.items:
        item_id = int(requested.firebird_warehouse_item_id)
        if item_id in seen:
            raise ValueError("Ta sama kartoteka tonera została wybrana więcej niż raz.")
        seen.add(item_id)
        warehouse = stock_by_id.get(item_id)
        if warehouse is None:
            raise ValueError(f"Pozycja {item_id} nie jest tonerem w magazynie wydającym.")
        if Decimal(str(warehouse["available_after_soft_reservations"])) < requested.quantity:
            raise ShippingConflictError(f"Brak dostępnego stanu: {warehouse['item_name']}.")
        selected.append((requested, warehouse))

    case.items.clear()
    case.address_id = address_row.id if address_row else None
    case.address_snapshot = address_data
    case.source_snapshot = order
    case.weight_kg = payload.weight_kg
    case.reviewed_by = user_id
    case.reviewed_at = now
    case.updated_at = now
    case.status = "ready"
    for requested, warehouse in selected:
        case.items.append(
            ShippingItem(
                shipping_case_id=case.id,
                firebird_warehouse_item_id=int(warehouse["warehouse_item_id"]),
                warehouse_id=int(warehouse["warehouse_id"]),
                item_index=warehouse.get("item_index"),
                item_name=warehouse["item_name"],
                unit=warehouse.get("unit") or "szt.",
                quantity=requested.quantity,
                price_net=Decimal(str(warehouse.get("price_net") or 0)),
                purchase_price_net=Decimal(str(warehouse.get("purchase_price_net") or 0)),
                vat_rate=Decimal(str(warehouse.get("vat_rate") or 23)),
                created_at=now,
            )
        )
        if requested.remember_for_model and order.get("model_id"):
            stmt = select(ShippingConsumableCompatibility).where(
                ShippingConsumableCompatibility.firebird_model_id == int(order["model_id"]),
                ShippingConsumableCompatibility.firebird_warehouse_item_id
                == int(warehouse["warehouse_item_id"]),
            )
            mapping = (await session.execute(stmt)).scalar_one_or_none()
            if mapping is None:
                session.add(
                    ShippingConsumableCompatibility(
                        firebird_model_id=int(order["model_id"]),
                        firebird_warehouse_item_id=int(warehouse["warehouse_item_id"]),
                        model_label=" ".join(
                            value
                            for value in (
                                order.get("device_brand") or order.get("machine_brand"),
                                order.get("device_model") or order.get("machine_model"),
                            )
                            if value
                        ),
                        item_index=warehouse.get("item_index"),
                        item_name=warehouse["item_name"],
                        confirmed_by=user_id,
                        created_at=now,
                    )
                )
    session.add(
        ShippingEvent(
            shipping_case_id=case.id,
            event_type="review_accepted",
            payload={"item_count": len(selected), "address_source": address_data["source"]},
            created_by=user_id,
            created_at=now,
        )
    )
    await session.commit()
    refreshed = await get_shipping_case(session, int(order["order_table_id"]))
    assert refreshed is not None
    return _serialize_case(refreshed)


async def create_shipping_shipment(
    session: AsyncSession,
    *,
    order_table_id: int,
    idempotency_key: str,
    user_id: int,
    manual_tracking: str | None = None,
) -> dict[str, Any]:
    """Tworzy etykietę DPD, zapisuje odpowiedź, a następnie aktualizuje Firebird."""
    existing_stmt = select(ShippingShipment).where(
        ShippingShipment.idempotency_key == idempotency_key
    )
    existing = (await session.execute(existing_stmt)).scalar_one_or_none()
    if existing:
        case = await get_shipping_case(session, order_table_id)
        if case is None or case.id != existing.shipping_case_id:
            raise ShippingConflictError("Klucz idempotencji został użyty dla innego zlecenia.")
        return _serialize_case(case)

    case = await get_shipping_case(session, order_table_id)
    if case is None or case.status != "ready":
        raise ShippingConflictError("Zlecenie wymaga wcześniejszej akceptacji danych wysyłki.")
    if case.shipment is not None:
        raise ShippingConflictError("Dla zlecenia istnieje już przesyłka.")
    dpd = DpdShippingClient()
    mode = "manual" if manual_tracking else ("mock" if dpd.test_mode else "live")
    if mode == "live":
        enabled, reason = firebird_writes_enabled()
        if not enabled:
            raise RuntimeError(reason or "Zapis do Firebird jest zablokowany.")
    now = _now()
    shipment = ShippingShipment(
        shipping_case=case,
        idempotency_key=idempotency_key,
        provider_mode=mode,
        status="processing",
        provider_request={},
        firebird_status="pending",
        created_by=user_id,
        created_at=now,
        updated_at=now,
    )
    session.add(shipment)
    await session.flush()
    session.add(
        ShippingEvent(
            shipping_case_id=case.id,
            shipment_id=shipment.id,
            event_type="shipment_started",
            payload={"provider_mode": mode},
            created_by=user_id,
            created_at=now,
        )
    )
    await session.commit()

    try:
        if manual_tracking:
            shipment.provider_shipment_id = manual_tracking.strip()
            shipment.tracking_number = manual_tracking.strip()
            shipment.provider_request = {"mode": "manual"}
            shipment.provider_response = {"mode": "manual"}
        else:
            request_payload, result = await asyncio.to_thread(
                dpd.create_shipment,
                idempotency_key=idempotency_key,
                reference=f"MS-{case.firebird_order_year}-{case.firebird_order_id}",
                receiver=case.address_snapshot,
                weight_kg=float(case.weight_kg),
            )
            shipment.provider_request = request_payload
            shipment.provider_response = result.raw_response
            shipment.provider_shipment_id = result.shipment_id
            shipment.tracking_number = result.tracking_number
            shipment.label_content = result.label_content
            shipment.label_content_type = result.label_content_type
            shipment.label_format = result.label_format
        shipment.status = "label_ready"
        shipment.updated_at = _now()
        await session.commit()
    except Exception as exc:
        shipment.status = "failed"
        shipment.error_text = str(exc)
        shipment.updated_at = _now()
        await session.commit()
        raise

    if mode == "mock":
        shipment.firebird_status = "simulated"
    else:
        try:
            result = await asyncio.to_thread(
                write_shipment_to_order,
                order_table_id=case.firebird_order_table_id,
                tracking_number=str(shipment.tracking_number),
                items=[
                    {
                        "firebird_warehouse_item_id": item.firebird_warehouse_item_id,
                        "quantity": float(item.quantity),
                    }
                    for item in case.items
                ],
            )
            shipment.firebird_status = result["status"]
            positions = result.get("created_position_ids", [])
            for item, position_id in zip(case.items, positions, strict=False):
                item.firebird_position_id = position_id
        except Exception as exc:
            shipment.firebird_status = "reconcile_required"
            shipment.firebird_error = str(exc)
            shipment.status = "reconcile_required"
            case.status = "reconcile_required"
            shipment.updated_at = _now()
            await session.commit()
            return _serialize_case(case)

    case.status = "shipment_created"
    case.updated_at = _now()
    shipment.updated_at = _now()
    session.add(
        ShippingEvent(
            shipping_case_id=case.id,
            shipment_id=shipment.id,
            event_type="shipment_created",
            payload={
                "tracking_number": shipment.tracking_number,
                "firebird_status": shipment.firebird_status,
            },
            created_by=user_id,
            created_at=_now(),
        )
    )
    await session.commit()
    refreshed = await get_shipping_case(session, order_table_id)
    assert refreshed is not None
    return _serialize_case(refreshed)


async def _send_notifications(shipment: ShippingShipment, case: ShippingCase) -> list[str]:
    errors: list[str] = []
    address = case.address_snapshot
    if shipment.provider_mode == "mock":
        shipment.notification_sms_status = "simulated"
        shipment.notification_email_status = "simulated" if address.get("email") else "skipped"
        return errors
    text = (
        f"Przesyłka DPD {shipment.tracking_number} została przekazana kurierowi. "
        "Status można sprawdzić na stronie DPD."
    )
    provider = HttpSmsProvider(
        base_url=settings.sms_api_url,
        token=settings.sms_api_token,
        sender=settings.sms_default_sender,
        username=settings.sms_api_username,
        password=settings.sms_api_password,
        sms_type=settings.sms_type,
        test_mode=settings.sms_test_mode,
        delivery_mode=settings.outbound_delivery_mode,
    )
    try:
        result = await asyncio.to_thread(
            provider.send_sms,
            str(address["phone"]),
            text,
            metadata={"source": "shipping_handover", "shipment_id": shipment.id},
        )
        shipment.notification_sms_status = "sent" if result.success else "failed"
        if not result.success:
            errors.append(result.error or "Nie udało się wysłać SMS.")
    except Exception as exc:
        shipment.notification_sms_status = "failed"
        errors.append(str(exc))

    recipient = address.get("email")
    if not recipient:
        shipment.notification_email_status = "skipped"
        return errors
    message = EmailMessage()
    sender_address = settings.email_sender_address or settings.email_username
    if not sender_address:
        shipment.notification_email_status = "failed"
        errors.append("Brak adresu nadawcy SMTP.")
        return errors
    message["From"] = formataddr((settings.email_sender_name or "Ksero-Partner", sender_address))
    message["To"] = recipient
    message["Subject"] = f"Przesyłka DPD {shipment.tracking_number}"
    message.set_content(text)
    result = await send_smtp_message(
        host=settings.email_host or "",
        port=settings.email_port,
        username=settings.email_username,
        password=settings.email_password,
        use_tls=settings.email_use_tls,
        use_ssl=settings.email_use_ssl,
        message=message,
        source="shipping_handover",
    )
    shipment.notification_email_status = "sent" if result.success else "failed"
    if not result.success:
        errors.append(result.message or "Nie udało się wysłać e-maila.")
    return errors


async def close_shipping_day(
    session: AsyncSession,
    *,
    business_date: date,
    user_id: int,
) -> dict[str, Any]:
    """Oznacza odbiór, tworzy RW dla umów i wysyła powiadomienia dokładnie raz."""
    existing_stmt = select(ShippingDayClose).where(ShippingDayClose.business_date == business_date)
    existing = (await session.execute(existing_stmt)).scalar_one_or_none()
    if existing and existing.status == "completed":
        return {
            "id": existing.id,
            "business_date": existing.business_date.isoformat(),
            "status": existing.status,
            **existing.summary,
        }
    from zoneinfo import ZoneInfo

    local_start = datetime.combine(business_date, time.min, tzinfo=ZoneInfo("Europe/Warsaw"))
    start = local_start.astimezone(UTC)
    end = (local_start + timedelta(days=1)).astimezone(UTC)
    stmt = (
        select(ShippingShipment)
        .options(selectinload(ShippingShipment.shipping_case).selectinload(ShippingCase.items))
        .where(
            ShippingShipment.created_at >= start,
            ShippingShipment.created_at < end,
            ShippingShipment.status.in_(("label_ready", "handed_over")),
        )
        .order_by(ShippingShipment.id)
    )
    shipments = list((await session.execute(stmt)).scalars().all())
    now = _now()
    day_close = existing or ShippingDayClose(
        business_date=business_date,
        status="processing",
        shipment_count=len(shipments),
        closed_count=0,
        error_count=0,
        summary={},
        closed_by=user_id,
        created_at=now,
    )
    if existing is None:
        session.add(day_close)
        await session.flush()
    errors: list[dict[str, Any]] = []
    closed_count = 0
    manual_billing_count = 0
    for shipment in shipments:
        case = shipment.shipping_case
        shipment.day_close_id = day_close.id
        if shipment.handed_over_at is None:
            shipment.handed_over_at = now
        shipment.status = "handed_over"
        case.status = "handed_over"
        try:
            if (case.order_kind or "").strip().casefold() == "umowa":
                if shipment.provider_mode == "mock":
                    rw_result = {"status": "simulated", "rw_id": None, "rw_number": None}
                else:
                    rw_result = await asyncio.to_thread(
                        create_rw_and_close_order,
                        order_table_id=case.firebird_order_table_id,
                        warehouse_id=settings.shipping_warehouse_id,
                        items=[
                            {
                                "firebird_warehouse_item_id": item.firebird_warehouse_item_id,
                                "quantity": float(item.quantity),
                            }
                            for item in case.items
                        ],
                    )
                case.status = "closed"
                shipment.status = "closed"
                shipment.closed_at = now
                closed_count += 1
            else:
                rw_result = {"status": "manual_billing", "rw_id": None, "rw_number": None}
                case.status = "manual_billing"
                manual_billing_count += 1
            notification_errors = await _send_notifications(shipment, case)
            if notification_errors:
                shipment.notification_error = "; ".join(notification_errors)
                errors.append(
                    {
                        "shipment_id": shipment.id,
                        "stage": "notification",
                        "error": shipment.notification_error,
                    }
                )
            session.add(
                ShippingEvent(
                    shipping_case_id=case.id,
                    shipment_id=shipment.id,
                    event_type="courier_handover",
                    payload={"rw": rw_result, "notification_errors": notification_errors},
                    created_by=user_id,
                    created_at=now,
                )
            )
        except Exception as exc:
            shipment.status = "reconcile_required"
            shipment.firebird_status = "reconcile_required"
            shipment.firebird_error = str(exc)
            case.status = "reconcile_required"
            errors.append({"shipment_id": shipment.id, "stage": "firebird_rw", "error": str(exc)})

    day_close.shipment_count = len(shipments)
    day_close.closed_count = closed_count
    day_close.error_count = len(errors)
    day_close.status = "completed" if not errors else "partial"
    day_close.completed_at = now
    day_close.summary = {
        "shipment_count": len(shipments),
        "closed_count": closed_count,
        "manual_billing_count": manual_billing_count,
        "error_count": len(errors),
        "errors": errors,
    }
    await session.commit()
    return {
        "id": day_close.id,
        "business_date": business_date.isoformat(),
        "status": day_close.status,
        **day_close.summary,
    }


__all__ = [
    "ShippingConflictError",
    "build_stock_payload",
    "close_shipping_day",
    "create_shipping_shipment",
    "get_shipping_case",
    "review_shipping_order",
    "serialize_shipping_case",
]
