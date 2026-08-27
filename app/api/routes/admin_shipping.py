"""API kolejki i realizacji wysyłek części przez DPD."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_admin_session_context, get_db_session
from app.core.config import settings
from app.models import (
    ShippingAddress,
    ShippingCase,
    ShippingConsumableCompatibility,
    ShippingShipment,
)
from app.schemas.shipping import (
    ShippingBulkCreateRequest,
    ShippingCompatibilityManualBatchRequest,
    ShippingCompatibilityManualRequest,
    ShippingCompatibilityReviewRequest,
    ShippingCompatibilityWebRequest,
    ShippingConsolidatedCreateRequest,
    ShippingCreateRequest,
    ShippingDayCloseRequest,
    ShippingManualTrackingRequest,
    ShippingOrderCloseRequest,
    ShippingReviewRequest,
)
from app.services import section_permissions
from app.services.audit import record_audit
from app.services.dpd_shipping import DpdConfigurationError, DpdShippingClient, DpdTransportError
from app.services.firebird_runtime import load_firebird_runtime_config, use_firebird_runtime_config
from app.services.shipping_archive import (
    get_shipping_archive_detail,
    list_shipping_archive,
)
from app.services.shipping_compatibility import (
    confirm_manual_compatibilities,
    confirm_manual_compatibility,
    list_compatibilities,
    list_compatibility_items,
    review_compatibilities,
    scan_compatibility_catalog,
    serialize_compatibility,
)
from app.services.shipping_compatibility_web import (
    CompatibilityWebError,
    enrich_compatibilities_with_web,
)
from app.services.shipping_documents import (
    build_mock_shipping_label,
    build_mock_shipping_label_sheet,
    build_shipping_packing_summary,
    merge_shipping_pdf_documents,
)
from app.services.shipping_firebird import (
    load_compatibility_catalog,
    load_device_models,
    load_shipping_order,
    load_shipping_order_state,
    load_shipping_overdue_invoices,
    load_shipping_overdue_summaries,
    load_shipping_queue,
    shipping_order_state_payload,
    validate_shipping_dictionary,
)
from app.services.shipping_workflow import (
    ShippingConflictError,
    build_shipping_address_candidates,
    build_shipping_consolidation_groups,
    build_stock_payload,
    close_shipping_day,
    close_shipping_order,
    create_consolidated_shipping_shipment,
    create_shipping_shipment,
    get_shipping_case,
    invalidate_shipping_case_for_location_change,
    review_shipping_order,
    serialize_shipping_case,
    shipping_location_context,
    shipping_shipment_consolidation,
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
    except OSError as exc:
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


def _parse_print_order_ids(order_table_ids: str) -> list[int]:
    """Waliduje uporządkowaną listę zleceń wybranych do wydruku."""
    try:
        requested_ids = list(
            dict.fromkeys(
                int(value.strip()) for value in order_table_ids.split(",") if value.strip()
            )
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lista zleceń ma niepoprawny format.",
        ) from exc
    if not requested_ids or len(requested_ids) > 100 or any(value <= 0 for value in requested_ids):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Wybierz od 1 do 100 poprawnych zleceń.",
        )
    return requested_ids


async def _load_print_cases(
    session: AsyncSession,
    requested_ids: list[int],
) -> list[ShippingCase]:
    """Pobiera wybrane sprawy i rozszerza je o zlecenia wspólnej paczki."""
    requested_cases = list(
        (
            await session.execute(
                select(ShippingCase)
                .options(selectinload(ShippingCase.items), selectinload(ShippingCase.shipment))
                .where(ShippingCase.firebird_order_table_id.in_(requested_ids))
            )
        )
        .scalars()
        .all()
    )
    requested_by_order_id = {case.firebird_order_table_id: case for case in requested_cases}
    expanded_ids = list(requested_ids)
    for order_id in requested_ids:
        case = requested_by_order_id.get(order_id)
        consolidation = shipping_shipment_consolidation(case.shipment if case else None)
        if not consolidation:
            continue
        for grouped_order_id in consolidation["order_table_ids"]:
            if grouped_order_id not in expanded_ids:
                expanded_ids.append(grouped_order_id)
    if len(expanded_ids) > 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Wspólne paczki rozszerzają wydruk do ponad 100 zleceń.",
        )
    cases = list(
        (
            await session.execute(
                select(ShippingCase)
                .options(selectinload(ShippingCase.items), selectinload(ShippingCase.shipment))
                .where(ShippingCase.firebird_order_table_id.in_(expanded_ids))
            )
        )
        .scalars()
        .all()
    )
    by_order_id = {case.firebird_order_table_id: case for case in cases}
    missing = [
        order_id
        for order_id in expanded_ids
        if order_id not in by_order_id
        or not by_order_id[order_id].shipment
        or not by_order_id[order_id].shipment.label_content
    ]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Brak etykiety dla zleceń: " + ", ".join(str(value) for value in missing) + ".",
        )
    return [by_order_id[order_id] for order_id in expanded_ids]


def _packing_entries(cases: list[ShippingCase]) -> list[dict[str, Any]]:
    """Buduje dane osobnego zestawienia magazynowego."""
    return [
        {
            "order_number": f"{case.firebird_order_id}/{case.firebird_order_year}",
            "address": case.address_snapshot,
            "tracking_number": case.shipment.tracking_number if case.shipment else None,
            "items": _shipping_case_label_items(case),
        }
        for case in cases
    ]


def _shipping_case_label_items(
    case: ShippingCase,
    *,
    include_order_number: bool = False,
) -> list[dict[str, Any]]:
    """Buduje pozycje jednej sprawy do etykiety i zestawienia pakowania."""
    order_number = f"{case.firebird_order_id}/{case.firebird_order_year}"
    return [
        {
            "order_number": order_number if include_order_number else None,
            "item_index": item.item_index,
            "item_name": item.item_name,
            "quantity": float(item.quantity),
            "unit": item.unit,
        }
        for item in case.items
    ]


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
        "test_firebird_writes": settings.shipping_test_firebird_writes_active,
        "weight_presets_kg": _weight_presets(),
        "default_weight_kg": settings.shipping_default_weight_kg,
        "courier_cutoff": cutoff.isoformat(),
        "after_cutoff": now >= cutoff,
        "compatibility_web": {
            "enabled": settings.shipping_compatibility_web_enabled,
            "batch_limit": min(settings.shipping_compatibility_web_batch_limit, 20),
            "daily_limit": settings.shipping_compatibility_web_daily_limit,
        },
    }


@router.get("/dpd/status", summary="Stan połączenia DPD Services REST")
async def shipping_dpd_status(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Zwraca bezpieczny stan konfiguracji DPD bez wykonywania operacji u przewoźnika."""
    await _require_shipping_access(admin_context, session)
    return DpdShippingClient().configuration_status()


@router.post("/dpd/demo-diagnostic", summary="Utwórz kontrolowaną etykietę DPD Demo")
async def shipping_dpd_demo_diagnostic(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> Response:
    """Tworzy przesyłkę Ksero-Partner → Ksero-Partner bez zapisu do Menadżera Serwisu."""
    admin_session, _ = admin_context
    user = await _require_shipping_access(admin_context, session)
    dpd = DpdShippingClient()
    if dpd.mode != "demo":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Diagnostyka jest dostępna wyłącznie przy `DPD_MODE=demo`.",
        )
    receiver = {
        "company_name": settings.dpd_sender_company,
        "contact_name": settings.dpd_sender_contact,
        "street": settings.dpd_sender_street,
        "postal_code": settings.dpd_sender_postal_code,
        "city": settings.dpd_sender_city,
        "country_code": "PL",
        "phone": settings.dpd_sender_phone,
        "email": settings.dpd_sender_email,
    }
    try:
        _, result = await asyncio.to_thread(
            dpd.create_shipment,
            idempotency_key=str(uuid4()),
            reference="CTIP-DEMO",
            receiver=receiver,
            weight_kg=1.0,
            items=[],
            business_references=["CTIP-DEMO"],
        )
    except DpdConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except DpdTransportError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    generated = result.raw_response.get("generate_packages") or {}
    labels = result.raw_response.get("generate_labels") or {}
    trace_id = str(labels.get("traceId") or generated.get("traceId") or "")
    await record_audit(
        session,
        user_id=user.id,
        action="shipping_dpd_demo_diagnostic",
        client_ip=admin_session.client_ip,
        payload={
            "tracking_number": result.tracking_number,
            "shipment_id": result.shipment_id,
            "trace_id": trace_id or None,
        },
    )
    await session.commit()
    headers = {
        "Content-Disposition": (f'inline; filename="DPD-DEMO-{result.tracking_number}-A4.pdf"'),
        "X-DPD-Waybill": result.tracking_number,
        "X-DPD-Session-Id": result.shipment_id,
    }
    if trace_id:
        headers["X-DPD-Trace-Id"] = trace_id
    return Response(
        content=result.label_content,
        media_type=result.label_content_type,
        headers=headers,
    )


@router.get("/compatibility", summary="Katalog zgodności części z modelami")
async def shipping_compatibility_list(
    mapping_status: Literal["suggested", "confirmed", "rejected", "stale"] | None = Query(
        default=None, alias="status"
    ),  # noqa: B008
    confidence: Literal["high", "medium", "low"] | None = Query(default=None),  # noqa: B008
    query: str | None = Query(default=None, max_length=200),  # noqa: B008
    page: int = Query(default=1, ge=1),  # noqa: B008
    page_size: int = Query(default=50, ge=1, le=200),  # noqa: B008
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Zwraca sugestie i ręczne decyzje z lokalnej bazy PostgreSQL."""
    await _require_shipping_access(admin_context, session)
    return await list_compatibilities(
        session,
        status=mapping_status,
        confidence=confidence,
        query=query,
        page=page,
        page_size=page_size,
    )


@router.get("/compatibility/items", summary="Katalog zgodności pogrupowany według części")
async def shipping_compatibility_items_list(
    mapping_status: Literal["suggested", "confirmed", "rejected", "stale"] | None = Query(
        default=None, alias="status"
    ),  # noqa: B008
    confidence: Literal["high", "medium", "low"] | None = Query(default=None),  # noqa: B008
    query: str | None = Query(default=None, max_length=200),  # noqa: B008
    page: int = Query(default=1, ge=1),  # noqa: B008
    page_size: int = Query(default=25, ge=1, le=100),  # noqa: B008
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Zwraca jedną pozycję katalogu dla części wraz z jej relacjami modeli."""
    await _require_shipping_access(admin_context, session)
    return await list_compatibility_items(
        session,
        status=mapping_status,
        confidence=confidence,
        query=query,
        page=page,
        page_size=page_size,
    )


@router.post("/compatibility/scan", summary="Przeskanuj nazwy i historię użycia")
async def shipping_compatibility_scan(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Odczytuje Firebird i zapisuje lokalne sugestie bez ich zatwierdzania."""
    admin_session, _ = admin_context
    user = await _require_shipping_access(admin_context, session)
    source = await _run_firebird(
        session,
        load_compatibility_catalog,
        warehouse_id=settings.shipping_warehouse_id,
    )
    result = await scan_compatibility_catalog(session, source=source)
    await record_audit(
        session,
        user_id=user.id,
        action="shipping_compatibility_scan",
        client_ip=admin_session.client_ip,
        payload=result,
    )
    await session.commit()
    return result


@router.post("/compatibility/review", summary="Zatwierdź lub odrzuć sugestie")
async def shipping_compatibility_review(
    payload: ShippingCompatibilityReviewRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Zapisuje zbiorczą decyzję administratora albo operatora."""
    admin_session, _ = admin_context
    user = await _require_shipping_access(admin_context, session)
    result = await review_compatibilities(
        session,
        mapping_ids=payload.mapping_ids,
        action=payload.action,
        user_id=user.id,
        note=payload.note,
    )
    await record_audit(
        session,
        user_id=user.id,
        action=f"shipping_compatibility_{payload.action}",
        client_ip=admin_session.client_ip,
        payload={**result, "mapping_ids": payload.mapping_ids},
    )
    await session.commit()
    return result


@router.post("/compatibility/manual", summary="Dodaj ręczne mapowanie")
async def shipping_compatibility_manual(
    payload: ShippingCompatibilityManualRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Potwierdza wskazany model i fizyczną kartotekę po ich walidacji w Firebird."""
    admin_session, _ = admin_context
    user = await _require_shipping_access(admin_context, session)
    source = await _run_firebird(
        session,
        load_compatibility_catalog,
        warehouse_id=settings.shipping_warehouse_id,
    )
    models = {
        int(model["id_model"]): model
        for model in source["models"]
        if model.get("id_model") is not None
    }
    items = {
        int(item["warehouse_item_id"]): item
        for item in source["items"]
        if item.get("warehouse_item_id") is not None
    }
    model = models.get(payload.firebird_model_id)
    if model is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Nieznany model Firebird."
        )
    item = items.get(payload.firebird_warehouse_item_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Kartoteka nie jest fizyczną pozycją magazynu głównego.",
        )
    row = await confirm_manual_compatibility(
        session,
        model_id=payload.firebird_model_id,
        model_label=" ".join(value for value in (model.get("marka"), model.get("model")) if value),
        item=item,
        user_id=user.id,
        note=payload.note,
    )
    await record_audit(
        session,
        user_id=user.id,
        action="shipping_compatibility_manual",
        client_ip=admin_session.client_ip,
        payload={"mapping_id": row.id},
    )
    await session.commit()
    return serialize_compatibility(row)


@router.post("/compatibility/manual-batch", summary="Dodaj wiele mapowań jednej części")
async def shipping_compatibility_manual_batch(
    payload: ShippingCompatibilityManualBatchRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Potwierdza wiele modeli dla jednej części po wspólnej walidacji w Firebird."""
    admin_session, _ = admin_context
    user = await _require_shipping_access(admin_context, session)
    source = await _run_firebird(
        session,
        load_compatibility_catalog,
        warehouse_id=settings.shipping_warehouse_id,
    )
    models = {
        int(model["id_model"]): model
        for model in source["models"]
        if model.get("id_model") is not None
    }
    missing_model_ids = [
        model_id for model_id in payload.firebird_model_ids if model_id not in models
    ]
    if missing_model_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Nieznane modele Firebird: {', '.join(map(str, missing_model_ids))}.",
        )
    items = {
        int(item["warehouse_item_id"]): item
        for item in source["items"]
        if item.get("warehouse_item_id") is not None
    }
    item = items.get(payload.firebird_warehouse_item_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Kartoteka nie jest fizyczną pozycją magazynu głównego.",
        )
    rows = await confirm_manual_compatibilities(
        session,
        models=[models[model_id] for model_id in payload.firebird_model_ids],
        item=item,
        user_id=user.id,
        note=payload.note,
    )
    mapping_ids = [int(row.id) for row in rows]
    await record_audit(
        session,
        user_id=user.id,
        action="shipping_compatibility_manual_batch",
        client_ip=admin_session.client_ip,
        payload={
            "mapping_ids": mapping_ids,
            "firebird_model_ids": payload.firebird_model_ids,
            "firebird_warehouse_item_id": payload.firebird_warehouse_item_id,
        },
    )
    await session.commit()
    return {
        "requested": len(payload.firebird_model_ids),
        "updated": len(rows),
        "items": [serialize_compatibility(row) for row in rows],
    }


@router.post("/compatibility/web", summary="Sprawdź wybrane części w WWW")
async def shipping_compatibility_web(
    payload: ShippingCompatibilityWebRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Ręcznie uruchamia Web Search i zapisuje cytowane wyniki jako sugestie."""
    admin_session, _ = admin_context
    user = await _require_shipping_access(admin_context, session)
    source = await _run_firebird(
        session,
        load_compatibility_catalog,
        warehouse_id=settings.shipping_warehouse_id,
    )
    try:
        result = await enrich_compatibilities_with_web(
            session,
            source=source,
            warehouse_item_ids=payload.warehouse_item_ids,
        )
    except CompatibilityWebError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await record_audit(
        session,
        user_id=user.id,
        action="shipping_compatibility_web",
        client_ip=admin_session.client_ip,
        payload={
            "requested_items": result["requested_items"],
            "created": result["created"],
            "refreshed": result["refreshed"],
            "response_id": result["response_id"],
        },
    )
    await session.commit()
    return result


@router.get("/stock", summary="Wyszukaj fizyczne kartoteki magazynu głównego")
async def shipping_stock_search(
    model_id: int | None = Query(default=None, gt=0),  # noqa: B008
    query: str | None = Query(default=None, max_length=200),  # noqa: B008
    compatible_only: bool = Query(default=False),  # noqa: B008
    only_available: bool = Query(default=False),  # noqa: B008
    limit: int = Query(default=200, ge=1, le=500),  # noqa: B008
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Przeszukuje części i towary z Firebirda oraz oznacza potwierdzoną zgodność."""
    await _require_shipping_access(admin_context, session)
    stock = await build_stock_payload(
        session,
        model_id=model_id,
        query=query,
        compatible_only=compatible_only,
        only_available=only_available,
        limit=limit,
    )
    item_ids = [int(item["warehouse_item_id"]) for item in stock]
    mappings_by_item: dict[int, list[ShippingConsumableCompatibility]] = {}
    if item_ids:
        mappings = (
            await session.execute(
                select(ShippingConsumableCompatibility).where(
                    ShippingConsumableCompatibility.firebird_warehouse_item_id.in_(item_ids)
                )
            )
        ).scalars()
        for mapping in mappings:
            mappings_by_item.setdefault(mapping.firebird_warehouse_item_id, []).append(mapping)
    for item in stock:
        mappings = mappings_by_item.get(int(item["warehouse_item_id"]), [])
        item["mapping_count"] = len(mappings)
        item["mapping_counts"] = {
            mapping_status: sum(1 for mapping in mappings if mapping.status == mapping_status)
            for mapping_status in ("suggested", "confirmed", "rejected", "stale")
            if any(mapping.status == mapping_status for mapping in mappings)
        }
        item["mapping_statuses"] = sorted({mapping.status for mapping in mappings})
        item["model_mappings"] = sorted(
            [
                {
                    "mapping_id": mapping.id,
                    "id": mapping.firebird_model_id,
                    "label": mapping.model_label,
                    "status": mapping.status,
                    "confidence": mapping.confidence,
                }
                for mapping in mappings
            ],
            key=lambda mapping: (mapping["status"] != "confirmed", mapping["label"]),
        )
        item["confirmed_models"] = sorted(
            [
                {"id": mapping.firebird_model_id, "label": mapping.model_label}
                for mapping in mappings
                if mapping.status == "confirmed"
            ],
            key=lambda model: model["label"],
        )
    return {"count": len(stock), "items": stock}


@router.get("/models", summary="Wyszukaj modele urządzeń")
async def shipping_models_search(
    query: str | None = Query(default=None, max_length=200),  # noqa: B008
    limit: int = Query(default=100, ge=1, le=500),  # noqa: B008
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Zwraca kanoniczne modele Firebird do ręcznego mapowania."""
    await _require_shipping_access(admin_context, session)
    rows = await _run_firebird(session, load_device_models, query=query, limit=limit)
    return {
        "count": len(rows),
        "items": [
            {
                "id": int(row["id_model"]),
                "brand": row.get("marka"),
                "model": row.get("model"),
                "label": " ".join(value for value in (row.get("marka"), row.get("model")) if value),
                "kind": row.get("rodzaj"),
            }
            for row in rows
        ],
    }


@router.get("/archive", summary="Archiwum zakończonych wysyłek")
async def shipping_archive_list(
    query: str | None = Query(default=None, max_length=200),  # noqa: B008
    date_from: date | None = Query(default=None),  # noqa: B008
    date_to: date | None = Query(default=None),  # noqa: B008
    operator_id: int | None = Query(default=None, gt=0),  # noqa: B008
    document_type: Literal["rw", "wz", "invoice"] | None = Query(default=None),  # noqa: B008
    source: Literal["mobile", "manual"] | None = Query(default=None),  # noqa: B008
    provider_mode: Literal["production", "demo", "mock", "manual"] | None = Query(  # noqa: B008
        default=None
    ),
    consolidated: bool | None = Query(default=None),  # noqa: B008
    archive_sort: Literal["newest", "oldest", "order", "client"] = Query(  # noqa: B008
        default="newest", alias="sort"
    ),
    page: int = Query(default=1, ge=1),  # noqa: B008
    page_size: int = Query(default=50, ge=1, le=200),  # noqa: B008
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Zwraca stronicowany rejestr bez wykonywania odczytów z Firebirda."""
    await _require_shipping_access(admin_context, session)
    if date_from and date_to and date_from > date_to:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Data początkowa nie może być późniejsza od daty końcowej.",
        )
    return await list_shipping_archive(
        session,
        query=query,
        date_from=date_from,
        date_to=date_to,
        operator_id=operator_id,
        document_type=document_type,
        source=source,
        provider_mode=provider_mode,
        consolidated=consolidated,
        sort=archive_sort,
        page=page,
        page_size=page_size,
    )


@router.get("/archive/{order_table_id}", summary="Szczegóły wpisu Archiwum")
async def shipping_archive_detail(
    order_table_id: int,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Zwraca snapshot, operatorów i historię zakończonego zlecenia."""
    await _require_shipping_access(admin_context, session)
    detail = await get_shipping_archive_detail(
        session,
        order_table_id=order_table_id,
    )
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Nie znaleziono zakończonego zlecenia w Archiwum.",
        )
    return detail


@router.get("/queue", summary="Kolejka zleceń dowozu materiałów")
async def shipping_queue(
    days: int = Query(default=30, ge=1, le=3650),  # noqa: B008
    limit: int = Query(default=200, ge=1, le=1000),  # noqa: B008
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Łączy kolejkę MS ze stanem CTIP, rozliczeniem i grupami wspólnych adresów."""
    await _require_shipping_access(admin_context, session)
    dictionary = await _run_firebird(session, validate_shipping_dictionary)
    if not dictionary["valid"]:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Słownik TYP_US nie potwierdza pozycji `dowóz materiałów` o ID 8.",
        )
    items = await _run_firebird(session, load_shipping_queue, days=days, limit=limit)
    customer_keys = {
        (int(item["company_id"]), int(item["client_id"]))
        for item in items
        if item.get("company_id") and item.get("client_id")
    }
    overdue_summaries = await _run_firebird(
        session,
        load_shipping_overdue_summaries,
        customer_keys,
    )
    order_ids = [int(item["order_table_id"]) for item in items]
    cases: dict[int, ShippingCase] = {}
    consolidation_groups: dict[int, dict[str, Any]] = {}
    if order_ids:
        rows = list(
            (
                await session.execute(
                    select(ShippingCase)
                    .options(selectinload(ShippingCase.shipment))
                    .where(ShippingCase.firebird_order_table_id.in_(order_ids))
                )
            )
            .scalars()
            .all()
        )
        cases = {row.firebird_order_table_id: row for row in rows}
        consolidation_groups = build_shipping_consolidation_groups(rows)
    for item in items:
        case = cases.get(int(item["order_table_id"]))
        item["ctip_status"] = case.status if case else "review_pending"
        item["case_id"] = case.id if case else None
        item["can_generate_label"] = bool(case and case.status == "ready" and case.shipment is None)
        item["label_available"] = bool(case and case.shipment and case.shipment.label_content)
        item["invoice_required"] = bool(case and case.invoice_required)
        item["consolidation"] = consolidation_groups.get(int(item["order_table_id"]))
        item["consolidated_shipment"] = shipping_shipment_consolidation(
            case.shipment if case else None
        )
        customer_key = (int(item["company_id"]), int(item["client_id"]))
        item["overdue_payment"] = overdue_summaries[customer_key]
    return {"days": days, "count": len(items), "items": items}


@router.get("/orders/{order_table_id}", summary="Dane do weryfikacji wysyłki")
async def shipping_order_detail(
    order_table_id: int,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Zwraca źródła adresu, zapisany adres, stan magazynu i bieżącą sprawę."""
    user = await _require_shipping_access(admin_context, session)
    order = await _run_firebird(session, load_shipping_order, order_table_id)
    overdue_payment = await _run_firebird(
        session,
        load_shipping_overdue_invoices,
        company_id=int(order["company_id"]),
        client_id=int(order["client_id"]),
    )
    case = await get_shipping_case(session, order_table_id)
    case_location_changed = await invalidate_shipping_case_for_location_change(
        session,
        case=case,
        order=order,
        user_id=user.id,
    )
    saved: list[ShippingAddress] = []
    if order.get("machine_id"):
        saved_stmt = (
            select(ShippingAddress)
            .where(
                ShippingAddress.firebird_client_id == int(order["client_id"]),
                ShippingAddress.firebird_machine_id == int(order["machine_id"]),
            )
            .order_by(ShippingAddress.updated_at.desc())
        )
        saved = list((await session.execute(saved_stmt)).scalars().all())
    address_candidates, preferred_candidate = build_shipping_address_candidates(
        order,
        saved,
        case,
    )
    location_context = shipping_location_context(order)
    location_context["case_location_changed"] = case_location_changed
    location_context["reviewed_for_current_location"] = bool(
        case
        and case.location_fingerprint
        and case.location_fingerprint == location_context["fingerprint"]
    )
    stock = await build_stock_payload(
        session,
        model_id=order.get("model_id"),
        exclude_case_id=case.id if case else None,
        compatible_only=bool(order.get("model_id")),
        include_item_ids={item.firebird_warehouse_item_id for item in case.items} if case else None,
    )
    return {
        "order": order,
        "order_state": shipping_order_state_payload(order),
        "preferred_address": preferred_candidate["address"],
        "preferred_address_key": preferred_candidate["key"],
        "address_candidates": address_candidates,
        "saved_address_available": any(
            candidate["key"].startswith("saved-") and candidate["selectable"]
            for candidate in address_candidates
        ),
        "location_context": location_context,
        "overdue_payment": overdue_payment,
        "stock": stock,
        "case": await serialize_shipping_case(session, order_table_id),
    }


@router.get("/orders/{order_table_id}/state", summary="Bieżący stan zlecenia w MS")
async def shipping_order_state(
    order_table_id: int,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Zwraca lekki stan zlecenia używany przez okresową kontrolę interfejsu."""
    await _require_shipping_access(admin_context, session)
    return await _run_firebird(session, load_shipping_order_state, order_table_id)


@router.post("/orders/{order_table_id}/review", summary="Zaakceptuj dane wysyłki")
async def shipping_order_review(
    order_table_id: int,
    payload: ShippingReviewRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Zapisuje zweryfikowany adres oraz miękką rezerwację części."""
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
    order = await _run_firebird(session, load_shipping_order, payload.order_table_id)
    runtime = await load_firebird_runtime_config(session)
    try:
        with use_firebird_runtime_config(runtime):
            result = await create_shipping_shipment(
                session,
                order=order,
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
    "/shipments/consolidated",
    status_code=status.HTTP_201_CREATED,
    summary="Utwórz jedną paczkę dla wielu zleceń",
)
async def shipping_consolidated_create(
    payload: ShippingConsolidatedCreateRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Tworzy jedną etykietę i przypisuje przesyłkę do kilku zgodnych zleceń."""
    admin_session, _ = admin_context
    user = await _require_shipping_access(admin_context, session)
    orders = [
        await _run_firebird(session, load_shipping_order, order_table_id)
        for order_table_id in payload.order_table_ids
    ]
    runtime = await load_firebird_runtime_config(session)
    try:
        with use_firebird_runtime_config(runtime):
            result = await create_consolidated_shipping_shipment(
                session,
                orders=orders,
                order_table_ids=payload.order_table_ids,
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
        action="shipping_consolidated_create",
        client_ip=admin_session.client_ip,
        payload={
            "order_table_ids": payload.order_table_ids,
            "group_id": result.get("group_id"),
            "tracking_number": result.get("tracking_number"),
        },
    )
    await session.commit()
    return result


@router.post(
    "/shipments/bulk",
    status_code=status.HTTP_201_CREATED,
    summary="Utwórz etykiety dla wielu zleceń",
)
async def shipping_bulk_create(
    payload: ShippingBulkCreateRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Generuje etykiety kolejno, raportując sukces albo błąd dla każdego zlecenia."""
    admin_session, _ = admin_context
    user = await _require_shipping_access(admin_context, session)
    if payload.all_ready:
        order_ids = list(
            (
                await session.execute(
                    select(ShippingCase.firebird_order_table_id)
                    .outerjoin(
                        ShippingShipment,
                        ShippingShipment.shipping_case_id == ShippingCase.id,
                    )
                    .where(
                        ShippingCase.status == "ready",
                        ShippingShipment.id.is_(None),
                    )
                    .order_by(ShippingCase.reviewed_at, ShippingCase.id)
                    .limit(101)
                )
            )
            .scalars()
            .all()
        )
        if len(order_ids) > 100:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Do wygenerowania jest ponad 100 etykiet. Użyj wyboru zleceń partiami.",
            )
    else:
        order_ids = payload.order_table_ids
    if not order_ids:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Brak gotowych zleceń bez etykiety.",
        )

    runtime = await load_firebird_runtime_config(session)
    created: list[dict] = []
    errors: list[dict[str, Any]] = []
    for order_table_id in order_ids:
        try:
            order = await _run_firebird(session, load_shipping_order, order_table_id)
            with use_firebird_runtime_config(runtime):
                result = await create_shipping_shipment(
                    session,
                    order=order,
                    order_table_id=order_table_id,
                    idempotency_key=str(uuid4()),
                    user_id=user.id,
                )
            created.append(result)
        except HTTPException as exc:
            errors.append({"order_table_id": order_table_id, "error": str(exc.detail)})
        except (
            ShippingConflictError,
            DpdConfigurationError,
            DpdTransportError,
            RuntimeError,
            ValueError,
        ) as exc:
            errors.append({"order_table_id": order_table_id, "error": str(exc)})

    printable_order_ids = [int(case["order_table_id"]) for case in created]
    warnings = list(
        dict.fromkeys(
            warning
            for case in created
            for warning in ((case.get("shipment") or {}).get("provider_warnings") or [])
        )
    )
    await record_audit(
        session,
        user_id=user.id,
        action="shipping_bulk_create",
        client_ip=admin_session.client_ip,
        payload={
            "requested_count": len(order_ids),
            "created_count": len(created),
            "error_count": len(errors),
            "order_table_ids": order_ids,
        },
    )
    await session.commit()
    return {
        "requested_count": len(order_ids),
        "created_count": len(created),
        "error_count": len(errors),
        "printable_order_ids": printable_order_ids,
        "warnings": warnings,
        "errors": errors,
    }


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
    order = await _run_firebird(session, load_shipping_order, payload.order_table_id)
    runtime = await load_firebird_runtime_config(session)
    try:
        with use_firebird_runtime_config(runtime):
            return await create_shipping_shipment(
                session,
                order=order,
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
    shipment = (
        await session.execute(
            select(ShippingShipment)
            .options(selectinload(ShippingShipment.shipping_case).selectinload(ShippingCase.items))
            .where(ShippingShipment.id == shipment_id)
        )
    ).scalar_one_or_none()
    if shipment is None or not shipment.label_content:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Brak etykiety.")
    tracking = shipment.tracking_number or str(shipment.id)
    label_content = shipment.label_content
    if shipment.provider_mode == "mock":
        consolidation = shipping_shipment_consolidation(shipment)
        label_cases = [shipment.shipping_case]
        if consolidation:
            rows = list(
                (
                    await session.execute(
                        select(ShippingCase)
                        .options(
                            selectinload(ShippingCase.items),
                            selectinload(ShippingCase.shipment),
                        )
                        .where(
                            ShippingCase.firebird_order_table_id.in_(
                                consolidation["order_table_ids"]
                            )
                        )
                    )
                )
                .scalars()
                .all()
            )
            by_order_id = {case.firebird_order_table_id: case for case in rows}
            label_cases = [
                by_order_id[order_id]
                for order_id in consolidation["order_table_ids"]
                if order_id in by_order_id
            ]
        label_content = build_mock_shipping_label(
            shipment.provider_request,
            tracking,
            [
                item
                for case in label_cases
                for item in _shipping_case_label_items(
                    case,
                    include_order_number=bool(consolidation),
                )
            ],
        )
    return Response(
        content=label_content,
        media_type=shipment.label_content_type or "application/pdf",
        headers={"Content-Disposition": f'inline; filename="DPD-{tracking}-A4.pdf"'},
    )


@router.get("/shipments/packing-list", summary="Drukuj zestawienie pakowania")
async def shipping_packing_list(
    order_table_ids: str = Query(min_length=1, max_length=2000),  # noqa: B008
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> Response:
    """Zwraca osobny dokument kompletacyjny na zwykły papier A4."""
    admin_session, _ = admin_context
    user = await _require_shipping_access(admin_context, session)
    requested_ids = _parse_print_order_ids(order_table_ids)
    cases = await _load_print_cases(session, requested_ids)
    content = build_shipping_packing_summary(_packing_entries(cases))
    await record_audit(
        session,
        user_id=user.id,
        action="shipping_packing_list_print",
        client_ip=admin_session.client_ip,
        payload={"order_table_ids": [case.firebird_order_table_id for case in cases]},
    )
    await session.commit()
    filename = f"DPD-kompletacja-{datetime.now(WARSAW):%Y%m%d-%H%M}.pdf"
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.get("/shipments/labels-sheet", summary="Drukuj arkusz etykiet DPD")
async def shipping_labels_sheet(
    order_table_ids: str = Query(min_length=1, max_length=2000),  # noqa: B008
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> Response:
    """Zwraca natywny arkusz DPD A4 albo lokalny odpowiednik 2×2 w trybie mock."""
    admin_session, _ = admin_context
    user = await _require_shipping_access(admin_context, session)
    requested_ids = _parse_print_order_ids(order_table_ids)
    cases = await _load_print_cases(session, requested_ids)
    shipments_by_tracking: dict[str, ShippingShipment] = {}
    cases_by_tracking: dict[str, list[ShippingCase]] = {}
    for case in cases:
        shipment = case.shipment
        tracking = str(shipment.tracking_number or "").strip() if shipment else ""
        if not shipment or not tracking:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Zlecenie {case.firebird_order_id}/{case.firebird_order_year} "
                    "nie ma numeru przesyłki."
                ),
            )
        shipments_by_tracking.setdefault(tracking, shipment)
        cases_by_tracking.setdefault(tracking, []).append(case)
    modes = {
        "production" if shipment.provider_mode == "live" else shipment.provider_mode
        for shipment in shipments_by_tracking.values()
    }
    document_id: str | None = None
    trace_id: str | None = None
    if modes == {"mock"}:
        content = build_mock_shipping_label_sheet(
            [
                {
                    "tracking_number": tracking,
                    "payload": shipment.provider_request,
                    "items": [
                        item
                        for case in cases_by_tracking[tracking]
                        for item in _shipping_case_label_items(
                            case,
                            include_order_number=len(cases_by_tracking[tracking]) > 1,
                        )
                    ],
                }
                for tracking, shipment in shipments_by_tracking.items()
            ]
        )
    elif len(modes) == 1 and modes <= {"demo", "production"}:
        provider_mode = next(iter(modes))
        configured = DpdShippingClient()
        if configured.mode != provider_mode:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Etykiety utworzono w trybie DPD {provider_mode}, a aplikacja działa teraz "
                    f"w trybie {configured.mode}. Przełącz konfigurację przed wydrukiem zbiorczym."
                ),
            )
        try:
            result = await asyncio.to_thread(
                configured.generate_label_sheet,
                list(shipments_by_tracking),
            )
        except DpdConfigurationError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except DpdTransportError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        content = result.label_content
        document_id = result.document_id
        trace_id = result.trace_id
    else:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Jedna partia wydruku nie może mieszać etykiet mock, demo i produkcyjnych.",
        )
    await record_audit(
        session,
        user_id=user.id,
        action="shipping_labels_sheet_print",
        client_ip=admin_session.client_ip,
        payload={
            "order_table_ids": [case.firebird_order_table_id for case in cases],
            "tracking_numbers": list(shipments_by_tracking),
            "provider_mode": next(iter(modes)),
            "document_id": document_id,
            "trace_id": trace_id,
        },
    )
    await session.commit()
    filename = f"DPD-etykiety-{datetime.now(WARSAW):%Y%m%d-%H%M}.pdf"
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.get("/shipments/print-bundle", summary="Drukuj etykiety i zestawienie pakowania")
async def shipping_print_bundle(
    order_table_ids: str = Query(min_length=1, max_length=2000),  # noqa: B008
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> Response:
    """Zwraca tabelę kompletacyjną i niezmienione strony etykiet przewoźnika."""
    await _require_shipping_access(admin_context, session)
    try:
        requested_ids = list(
            dict.fromkeys(
                int(value.strip()) for value in order_table_ids.split(",") if value.strip()
            )
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lista zleceń ma niepoprawny format.",
        ) from exc
    if not requested_ids or len(requested_ids) > 100 or any(value <= 0 for value in requested_ids):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Wybierz od 1 do 100 poprawnych zleceń.",
        )
    requested_cases = list(
        (
            await session.execute(
                select(ShippingCase)
                .options(selectinload(ShippingCase.items), selectinload(ShippingCase.shipment))
                .where(ShippingCase.firebird_order_table_id.in_(requested_ids))
            )
        )
        .scalars()
        .all()
    )
    requested_by_order_id = {case.firebird_order_table_id: case for case in requested_cases}
    expanded_ids = list(requested_ids)
    for order_id in requested_ids:
        case = requested_by_order_id.get(order_id)
        consolidation = shipping_shipment_consolidation(case.shipment if case else None)
        if not consolidation:
            continue
        for grouped_order_id in consolidation["order_table_ids"]:
            if grouped_order_id not in expanded_ids:
                expanded_ids.append(grouped_order_id)
    if len(expanded_ids) > 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Wspólne paczki rozszerzają wydruk do ponad 100 zleceń.",
        )
    cases = list(
        (
            await session.execute(
                select(ShippingCase)
                .options(
                    selectinload(ShippingCase.items),
                    selectinload(ShippingCase.shipment),
                )
                .where(ShippingCase.firebird_order_table_id.in_(expanded_ids))
            )
        )
        .scalars()
        .all()
    )
    by_order_id = {case.firebird_order_table_id: case for case in cases}
    ordered_cases = [by_order_id[order_id] for order_id in expanded_ids if order_id in by_order_id]
    missing = [
        order_id
        for order_id in expanded_ids
        if order_id not in by_order_id
        or not by_order_id[order_id].shipment
        or not by_order_id[order_id].shipment.label_content
    ]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Brak etykiety dla zleceń: " + ", ".join(str(value) for value in missing) + ".",
        )
    entries: list[dict[str, Any]] = []
    for case in ordered_cases:
        shipment = case.shipment
        assert shipment is not None and shipment.label_content is not None
        items = _shipping_case_label_items(case)
        entries.append(
            {
                "order_number": f"{case.firebird_order_id}/{case.firebird_order_year}",
                "address": case.address_snapshot,
                "tracking_number": shipment.tracking_number,
                "items": items,
            }
        )
    label_groups: dict[str, list[ShippingCase]] = {}
    for case in ordered_cases:
        shipment = case.shipment
        assert shipment is not None
        consolidation = shipping_shipment_consolidation(shipment)
        group_key = (
            f"consolidated:{consolidation.get('group_id') or shipment.provider_shipment_id}"
            if consolidation
            else f"shipment:{shipment.id}"
        )
        label_groups.setdefault(group_key, []).append(case)

    labels: list[bytes] = []
    for grouped_cases in label_groups.values():
        shipment = grouped_cases[0].shipment
        assert shipment is not None and shipment.label_content is not None
        if shipment.provider_mode == "mock":
            consolidated = len(grouped_cases) > 1
            labels.append(
                build_mock_shipping_label(
                    shipment.provider_request,
                    shipment.tracking_number or str(shipment.id),
                    [
                        item
                        for case in grouped_cases
                        for item in _shipping_case_label_items(
                            case,
                            include_order_number=consolidated,
                        )
                    ],
                )
            )
        else:
            labels.append(shipment.label_content)
    summary = build_shipping_packing_summary(entries)
    content = merge_shipping_pdf_documents([summary, *labels])
    filename = f"DPD-zestaw-{datetime.now(WARSAW):%Y%m%d-%H%M}.pdf"
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
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


@router.post("/orders/{order_table_id}/close", summary="Zakończ pojedyncze zlecenie")
async def shipping_order_close_execute(
    order_table_id: int,
    payload: ShippingOrderCloseRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Potwierdza odbiór jednej paczki i uruchamia właściwy proces RW, WZ albo FV."""
    admin_session, _ = admin_context
    user = await _require_shipping_access(admin_context, session)
    if not payload.confirm_handover:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Wymagane jest potwierdzenie fizycznego przekazania paczki kurierowi.",
        )
    runtime = await load_firebird_runtime_config(session)
    try:
        with use_firebird_runtime_config(runtime):
            result = await close_shipping_order(
                session,
                order_table_id=order_table_id,
                user_id=user.id,
            )
    except ShippingConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    await record_audit(
        session,
        user_id=user.id,
        action="shipping_order_close",
        client_ip=admin_session.client_ip,
        payload={"order_table_id": order_table_id, **result},
    )
    await session.commit()
    return result


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
