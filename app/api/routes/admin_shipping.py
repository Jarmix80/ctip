"""API kolejki i realizacji wysyłek części przez DPD."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_session_context, get_db_session
from app.core.config import settings
from app.models import ShippingAddress, ShippingCase, ShippingShipment
from app.schemas.shipping import (
    ShippingCreateRequest,
    ShippingDayCloseRequest,
    ShippingManualTrackingRequest,
    ShippingReviewRequest,
)
from app.services import section_permissions
from app.services.audit import record_audit
from app.services.dpd_shipping import DpdConfigurationError, DpdShippingClient, DpdTransportError
from app.services.firebird_runtime import load_firebird_runtime_config, use_firebird_runtime_config
from app.services.shipping_firebird import (
    load_shipping_order,
    load_shipping_queue,
    validate_shipping_dictionary,
)
from app.services.shipping_workflow import (
    ShippingConflictError,
    build_stock_payload,
    close_shipping_day,
    create_shipping_shipment,
    get_shipping_case,
    review_shipping_order,
    serialize_shipping_case,
)

router = APIRouter(prefix="/admin/shipping", tags=["admin-shipping"])
WARSAW = ZoneInfo("Europe/Warsaw")


async def _require_shipping_access(
    admin_context,
    session: AsyncSession,
):
    _, user = admin_context
    if user.role not in {"admin", "operator"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operacja wymaga roli administratora lub operatora.",
        )
    if not await section_permissions.user_has_section(session, user, "shipping"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Konto nie ma uprawnień do sekcji wysyłek.",
        )
    return user


async def _run_firebird(session: AsyncSession, function, /, *args, **kwargs):
    runtime = await load_firebird_runtime_config(session)

    def run():
        with use_firebird_runtime_config(runtime):
            return function(*args, **kwargs)

    try:
        return await asyncio.to_thread(run)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


def _weight_presets() -> list[float]:
    values: list[float] = []
    for item in settings.shipping_weight_presets_raw.split(","):
        try:
            value = float(item.strip().replace(",", "."))
        except ValueError:
            continue
        if 0 < value <= 31.5 and value not in values:
            values.append(value)
    return values or [float(settings.shipping_default_weight_kg)]


@router.get("/config", summary="Stan konfiguracji wysyłek")
async def shipping_config(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Zwraca jawne ustawienia operacyjne bez sekretów DPD."""
    await _require_shipping_access(admin_context, session)
    dictionary = await _run_firebird(session, validate_shipping_dictionary)
    now = datetime.now(WARSAW)
    cutoff = now.replace(
        hour=settings.shipping_courier_cutoff_hour,
        minute=settings.shipping_courier_cutoff_minute,
        second=0,
        microsecond=0,
    )
    return {
        "dpd": DpdShippingClient().configuration_status(),
        "firebird_dictionary": dictionary,
        "warehouse_id": settings.shipping_warehouse_id,
        "weight_presets_kg": _weight_presets(),
        "default_weight_kg": settings.shipping_default_weight_kg,
        "courier_cutoff": cutoff.isoformat(),
        "after_cutoff": now >= cutoff,
    }


@router.get("/queue", summary="Kolejka zleceń dowozu materiałów")
async def shipping_queue(
    days: int = Query(default=30, ge=1, le=3650),  # noqa: B008
    limit: int = Query(default=200, ge=1, le=1000),  # noqa: B008
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Łączy kolejkę z Firebirda z lokalnym stanem procesu CTIP."""
    await _require_shipping_access(admin_context, session)
    dictionary = await _run_firebird(session, validate_shipping_dictionary)
    if not dictionary["valid"]:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Słownik TYP_US nie potwierdza pozycji `dowóz materiałów` o ID 8.",
        )
    items = await _run_firebird(session, load_shipping_queue, days=days, limit=limit)
    order_ids = [int(item["order_table_id"]) for item in items]
    cases: dict[int, ShippingCase] = {}
    if order_ids:
        rows = (
            await session.execute(
                select(ShippingCase).where(ShippingCase.firebird_order_table_id.in_(order_ids))
            )
        ).scalars()
        cases = {row.firebird_order_table_id: row for row in rows}
    for item in items:
        case = cases.get(int(item["order_table_id"]))
        item["ctip_status"] = case.status if case else "review_pending"
        item["case_id"] = case.id if case else None
    return {"days": days, "count": len(items), "items": items}


@router.get("/orders/{order_table_id}", summary="Dane do weryfikacji wysyłki")
async def shipping_order_detail(
    order_table_id: int,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Zwraca źródła adresu, zapisany adres, stan magazynu i bieżącą sprawę."""
    await _require_shipping_access(admin_context, session)
    order = await _run_firebird(session, load_shipping_order, order_table_id)
    case = await get_shipping_case(session, order_table_id)
    saved_stmt = (
        select(ShippingAddress)
        .where(ShippingAddress.firebird_client_id == int(order["client_id"]))
        .order_by(ShippingAddress.updated_at.desc())
    )
    saved = list((await session.execute(saved_stmt)).scalars().all())
    exact = next(
        (
            address
            for address in saved
            if address.firebird_machine_id
            and address.firebird_machine_id == order.get("machine_id")
        ),
        None,
    )
    selected_saved = exact or (saved[0] if saved else None)
    saved_payload = None
    if selected_saved:
        saved_payload = {
            "company_name": selected_saved.company_name,
            "contact_name": selected_saved.contact_name,
            "street": selected_saved.street,
            "postal_code": selected_saved.postal_code,
            "city": selected_saved.city,
            "country_code": selected_saved.country_code,
            "phone": selected_saved.phone,
            "email": selected_saved.email,
            "source": "saved",
            "location_text": order.get("order_location") or order.get("machine_location"),
        }
    stock = await build_stock_payload(
        session,
        model_id=order.get("model_id"),
        exclude_case_id=case.id if case else None,
    )
    return {
        "order": order,
        "preferred_address": saved_payload or order["suggested_address"],
        "saved_address_available": bool(saved_payload),
        "stock": stock,
        "case": await serialize_shipping_case(session, order_table_id),
    }


@router.post("/orders/{order_table_id}/review", summary="Zaakceptuj dane wysyłki")
async def shipping_order_review(
    order_table_id: int,
    payload: ShippingReviewRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Zapisuje zweryfikowany adres oraz miękką rezerwację tonerów."""
    admin_session, _ = admin_context
    user = await _require_shipping_access(admin_context, session)
    order = await _run_firebird(session, load_shipping_order, order_table_id)
    try:
        result = await review_shipping_order(
            session,
            order=order,
            payload=payload,
            user_id=user.id,
        )
    except ShippingConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await record_audit(
        session,
        user_id=user.id,
        action="shipping_review_accept",
        client_ip=admin_session.client_ip,
        payload={"order_table_id": order_table_id, "case_id": result["id"]},
    )
    await session.commit()
    return result


@router.post("/shipments", status_code=status.HTTP_201_CREATED, summary="Utwórz przesyłkę DPD")
async def shipping_create(
    payload: ShippingCreateRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Generuje przesyłkę i etykietę, po czym aktualizuje zlecenie Firebird."""
    admin_session, _ = admin_context
    user = await _require_shipping_access(admin_context, session)
    runtime = await load_firebird_runtime_config(session)
    try:
        with use_firebird_runtime_config(runtime):
            result = await create_shipping_shipment(
                session,
                order_table_id=payload.order_table_id,
                idempotency_key=str(payload.idempotency_key),
                user_id=user.id,
            )
    except ShippingConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (DpdConfigurationError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except DpdTransportError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    await record_audit(
        session,
        user_id=user.id,
        action="shipping_create",
        client_ip=admin_session.client_ip,
        payload={
            "order_table_id": payload.order_table_id,
            "shipment_id": result.get("shipment", {}).get("id") if result.get("shipment") else None,
        },
    )
    await session.commit()
    return result


@router.post(
    "/shipments/manual-tracking",
    status_code=status.HTTP_201_CREATED,
    summary="Zarejestruj ręczny numer przesyłki",
)
async def shipping_manual_tracking(
    payload: ShippingManualTrackingRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Rejestruje wyjątek utworzony ręcznie w panelu DPD."""
    user = await _require_shipping_access(admin_context, session)
    runtime = await load_firebird_runtime_config(session)
    try:
        with use_firebird_runtime_config(runtime):
            return await create_shipping_shipment(
                session,
                order_table_id=payload.order_table_id,
                idempotency_key=str(payload.idempotency_key),
                user_id=user.id,
                manual_tracking=payload.tracking_number,
            )
    except ShippingConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@router.get("/shipments/{shipment_id}/label", summary="Pobierz etykietę A4")
async def shipping_label(
    shipment_id: int,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> Response:
    """Zwraca zapisaną etykietę bez ponownego tworzenia przesyłki."""
    await _require_shipping_access(admin_context, session)
    shipment = await session.get(ShippingShipment, shipment_id)
    if shipment is None or not shipment.label_content:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Brak etykiety.")
    tracking = shipment.tracking_number or str(shipment.id)
    return Response(
        content=shipment.label_content,
        media_type=shipment.label_content_type or "application/pdf",
        headers={"Content-Disposition": f'inline; filename="DPD-{tracking}-A4.pdf"'},
    )


@router.get("/day-close", summary="Podgląd paczek do zamknięcia dnia")
async def shipping_day_close_preview(
    business_date: date = Query(default_factory=date.today),  # noqa: B008
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Zwraca paczki z etykietą oczekujące na przekazanie kurierowi."""
    await _require_shipping_access(admin_context, session)
    local_start = datetime.combine(business_date, datetime.min.time(), tzinfo=WARSAW)
    start = local_start.astimezone(UTC)
    end = (local_start + timedelta(days=1)).astimezone(UTC)
    rows = (
        await session.execute(
            select(ShippingShipment).where(
                ShippingShipment.created_at >= start,
                ShippingShipment.created_at < end,
                ShippingShipment.status.in_(("label_ready", "handed_over")),
            )
        )
    ).scalars()
    shipments = [
        {
            "id": row.id,
            "tracking_number": row.tracking_number,
            "status": row.status,
            "provider_mode": row.provider_mode,
        }
        for row in rows
    ]
    return {"business_date": business_date.isoformat(), "count": len(shipments), "items": shipments}


@router.post("/day-close", summary="Potwierdź odbiór paczek przez kuriera")
async def shipping_day_close_execute(
    payload: ShippingDayCloseRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Finalizuje paczki, generuje RW dla umów i uruchamia powiadomienia."""
    admin_session, _ = admin_context
    user = await _require_shipping_access(admin_context, session)
    if not payload.confirm_handover:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Wymagane jest potwierdzenie fizycznego przekazania paczek kurierowi.",
        )
    runtime = await load_firebird_runtime_config(session)
    try:
        with use_firebird_runtime_config(runtime):
            result = await close_shipping_day(
                session,
                business_date=payload.business_date,
                user_id=user.id,
            )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    await record_audit(
        session,
        user_id=user.id,
        action="shipping_day_close",
        client_ip=admin_session.client_ip,
        payload=result,
    )
    await session.commit()
    return result


__all__ = ["router"]
