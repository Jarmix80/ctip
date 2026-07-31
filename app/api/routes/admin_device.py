"""API przyjęć, magazynu i audytu urządzeń."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_session_context, get_db_session
from app.core.config import settings
from app.models import (
    AdminSetting,
    DeviceAuditItem,
    DeviceAuditRun,
    DeviceCounterReading,
    DeviceIntakeOperation,
    DeviceInventoryEvent,
    DeviceInventoryUnit,
    DeviceSheetOutbox,
)
from app.services import firebird_ms_users, section_permissions
from app.services.audit import record_audit
from app.services.contracts_dashboard import load_available_devices_from_firebird_warehouse
from app.services.device_bnp_buyout import (
    complete_bnp_buyout,
    create_bnp_catalog_item,
    lookup_bnp_buyout,
)
from app.services.device_dashboard import load_device_dashboard_payload
from app.services.device_intake import (
    DeviceIntakeBatchResult,
    DeviceIntakeItemInput,
    create_device_intake_batch,
    create_device_model,
    create_device_supplier,
    get_next_ewidencja_suggestion,
    load_device_model_taxonomy,
    search_device_models,
    search_device_suppliers,
    update_device_machine_counters,
)
from app.services.device_registry import (
    DeviceIdempotencyConflict,
    DeviceReservationConflict,
    add_device_note,
    begin_intake_operation,
    canonical_request_hash,
    complete_intake_operation,
    enqueue_sheet_operation,
    ensure_inventory_unit_for_legacy,
    find_intake_operation,
    find_unit_by_source_or_identity,
    get_active_manual_reservation,
    intake_operation_payload,
    mark_intake_operation_failed,
    release_manual_reservation,
    save_manual_reservation,
)
from app.services.device_sheet_worker import retry_device_sheet_outbox
from app.services.device_warehouse import (
    build_device_warehouse_payload,
    serialize_device_events,
)
from app.services.device_withdrawal import (
    preview_device_pz_withdrawal,
    withdraw_device_pz,
)
from app.services.firebird_runtime import (
    firebird_writes_enabled,
    load_firebird_runtime_config,
    use_firebird_runtime_config,
)

router = APIRouter(prefix="/admin/device", tags=["admin-device"])
_DEVICE_AUDIT_CREATE_LOCK = asyncio.Lock()

DEVICE_BRANDS_SETTING_KEY = "device.model_brands"
DEFAULT_DEVICE_BRANDS = [
    "Canon",
    "Epson",
    "Konica Minolta",
    "Develop",
    "Kyocera",
    "Utax",
    "Ricoh",
    "Nashuatec",
]


class StrictRequest(BaseModel):
    """Bazowy model odrzucający historyczne i nieznane pola zapisu."""

    model_config = ConfigDict(extra="forbid")


class DeviceCatalogSyncRequest(StrictRequest):
    """Historyczny payload wyłączonej synchronizacji kartotek AUTO."""

    model_ids: list[int] | None = None
    only_missing: bool = True


class DeviceIntakeBatchItemRequest(StrictRequest):
    """Pojedynczy fizyczny egzemplarz przyjmowany dokumentem PZ."""

    model_id: int = Field(gt=0)
    serial: str = Field(min_length=1, max_length=100)
    ewidencja: str | None = Field(default=None, max_length=100)
    purchase_price_netto: Decimal = Field(ge=0, decimal_places=4)
    counter_bw: int | None = Field(default=None, ge=0)
    counter_color: int | None = Field(default=None, ge=0)
    counter_scan: int | None = Field(default=None, ge=0)


class DeviceIntakeBatchRequest(StrictRequest):
    """Idempotentne przyjęcie wielu urządzeń jednym dokumentem PZ."""

    idempotency_key: UUID
    items: list[DeviceIntakeBatchItemRequest] = Field(min_length=1, max_length=200)
    supplier_id: int = Field(gt=0)
    external_document: str | None = Field(default=None, max_length=30)
    document_date: date | None = None
    issue_date: date | None = None
    payment_method: str = Field(default="Przelew", min_length=1, max_length=30)
    payment_due_date: date | None = None
    allow_exception: bool = False
    exception_reason: str | None = Field(default=None, max_length=1000)
    ewidencja_prefix: str | None = Field(default="KP/", max_length=50)


class DeviceIntakeRequest(StrictRequest):
    """Zgodnościowy wariant przyjęcia jednego urządzenia."""

    idempotency_key: UUID
    model_id: int = Field(gt=0)
    serial: str = Field(min_length=1, max_length=100)
    ewidencja: str | None = Field(default=None, max_length=100)
    purchase_price_netto: Decimal = Field(ge=0, decimal_places=4)
    supplier_id: int = Field(gt=0)
    external_document: str | None = Field(default=None, max_length=30)
    document_date: date | None = None
    issue_date: date | None = None
    payment_method: str = Field(default="Przelew", min_length=1, max_length=30)
    payment_due_date: date | None = None
    allow_exception: bool = False
    exception_reason: str | None = Field(default=None, max_length=1000)
    ewidencja_prefix: str | None = Field(default="KP/", max_length=50)


class DeviceSupplierCreateRequest(StrictRequest):
    """Dane dostawcy tworzonego w tabeli KLIENT."""

    name: str = Field(min_length=1, max_length=500)
    nip: str | None = Field(default=None, max_length=30)
    address: str | None = Field(default=None, max_length=250)
    postal_code: str | None = Field(default=None, max_length=6)
    city: str | None = Field(default=None, max_length=150)
    phone: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=200)


class DeviceModelCreateRequest(StrictRequest):
    """Kompletne dane modelu bez wspólnej kartoteki magazynowej."""

    marka: str = Field(min_length=1, max_length=50)
    model: str = Field(min_length=1, max_length=50)
    grupa: str = Field(min_length=1, max_length=50)
    rodzaj: str = Field(min_length=1, max_length=50)
    kolor: bool
    plik: str | None = Field(default=None, max_length=250)


class DeviceNoteRequest(StrictRequest):
    """Treść bieżącej uwagi egzemplarza."""

    note: str = Field(min_length=3, max_length=2000)


class DeviceReservationRequest(StrictRequest):
    """Dane ręcznej rezerwacji egzemplarza."""

    reserved_for: str = Field(min_length=2, max_length=500)
    reason: str = Field(min_length=10, max_length=2000)
    expires_at: datetime | None = None


class DeviceReservationReleaseRequest(StrictRequest):
    """Uzasadnienie ręcznego zwolnienia rezerwacji."""

    reason: str = Field(min_length=10, max_length=2000)


class DeviceCounterReadingRequest(StrictRequest):
    """Ręczny, datowany odczyt liczników urządzenia."""

    reading_at: datetime
    counter_bw: int | None = Field(default=None, ge=0)
    counter_color: int | None = Field(default=None, ge=0)
    counter_scan: int | None = Field(default=None, ge=0)
    allow_lower: bool = False
    override_reason: str | None = Field(default=None, max_length=1000)
    note: str | None = Field(default=None, max_length=2000)


class DevicePzWithdrawalRequest(StrictRequest):
    """Potwierdzenie kontrolowanego wycofania dokumentu PZ."""

    confirmation: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=10, max_length=2000)
    force: bool = False


class DeviceBnpCatalogCreateRequest(StrictRequest):
    """Dane utworzenia brakującej kartoteki wykupu BNP."""

    serial: str = Field(min_length=1, max_length=100)
    machine_table_id: int = Field(gt=0)
    expected_ewidencja: str = Field(min_length=1, max_length=100)
    warehouse_index: str = Field(min_length=1, max_length=100)
    item_name: str = Field(min_length=1, max_length=250)


class DeviceBnpBuyoutCompleteRequest(StrictRequest):
    """Dane finalizacji wykupu urządzenia BNP."""

    serial: str = Field(min_length=1, max_length=100)
    machine_table_id: int = Field(gt=0)
    warehouse_item_id: int = Field(gt=0)
    expected_ewidencja: str = Field(min_length=1, max_length=100)
    target_ewidencja: str = Field(min_length=1, max_length=100)
    warehouse_index: str = Field(min_length=1, max_length=100)
    item_name: str = Field(min_length=1, max_length=250)
    external_document: str = Field(min_length=1, max_length=30)
    document_date: date
    purchase_price_netto: Decimal = Field(gt=0, decimal_places=4)


async def _get_or_seed_device_brands(session: AsyncSession) -> list[str]:
    stmt = select(AdminSetting).where(AdminSetting.key == DEVICE_BRANDS_SETTING_KEY)
    setting_row = (await session.execute(stmt)).scalars().first()
    if setting_row is None:
        setting_row = AdminSetting(
            key=DEVICE_BRANDS_SETTING_KEY,
            value=json.dumps(DEFAULT_DEVICE_BRANDS, ensure_ascii=False),
            is_secret=False,
        )
        session.add(setting_row)
        await session.commit()
        return list(DEFAULT_DEVICE_BRANDS)
    try:
        decoded = json.loads(setting_row.value)
    except json.JSONDecodeError:
        decoded = []
    brands = [str(item).strip() for item in decoded if str(item).strip()]
    if brands:
        return brands
    setting_row.value = json.dumps(DEFAULT_DEVICE_BRANDS, ensure_ascii=False)
    await session.commit()
    return list(DEFAULT_DEVICE_BRANDS)


async def _ensure_device_access(session: AsyncSession, admin_user) -> None:
    if not await section_permissions.user_has_section(session, admin_user, "device"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Konto nie ma prawa „Obsługa urządzeń”.",
        )


async def _ensure_device_writer(session: AsyncSession, admin_user):
    await _ensure_device_access(session, admin_user)
    if admin_user.firebird_app_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Konto nie jest powiązane z użytkownikiem Menadżera Serwisu.",
        )
    try:
        return await firebird_ms_users.resolve_firebird_ms_user(
            session,
            admin_user.firebird_app_user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


async def _ensure_pz_withdrawal_permission(session: AsyncSession, admin_user) -> None:
    await _ensure_device_access(session, admin_user)
    if admin_user.role != "admin" and not admin_user.can_withdraw_device_pz:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Konto nie ma prawa do wycofywania dokumentów PZ.",
        )


async def _run_firebird_read(session: AsyncSession, function, /, **kwargs):
    runtime = await load_firebird_runtime_config(session)
    with use_firebird_runtime_config(runtime):
        return await asyncio.to_thread(function, **kwargs)


async def _ensure_firebird_write_enabled(session: AsyncSession):
    runtime = await load_firebird_runtime_config(session)
    with use_firebird_runtime_config(runtime):
        enabled, reason = firebird_writes_enabled()
    if not enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=reason or "Zapis do Firebird jest zablokowany.",
        )
    return runtime


async def _load_available_warehouse_row(
    session: AsyncSession,
    source_row: int,
) -> dict[str, Any]:
    try:
        rows = await _run_firebird_read(
            session,
            load_available_devices_from_firebird_warehouse,
            limit=1,
            source_row=source_row,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Błąd odczytu egzemplarza z magazynu Firebird: {exc}",
        ) from exc
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Egzemplarz nie jest dostępny w magazynie urządzeń.",
        )
    return dict(rows[0])


async def _ensure_registered_warehouse_unit(
    session: AsyncSession,
    *,
    source_row: int,
) -> tuple[Any, dict[str, Any]]:
    source = await _load_available_warehouse_row(session, source_row)
    unit = await find_unit_by_source_or_identity(
        session,
        source_row=source_row,
        serial=source.get("serial"),
        ewidencja=source.get("ewidencja"),
    )
    if unit is None:
        try:
            unit = await ensure_inventory_unit_for_legacy(
                session,
                source_row=source_row,
                snapshot=source,
            )
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Nie można zarejestrować historycznego egzemplarza, ponieważ serial "
                    "lub numer KP jest już powiązany z innym wpisem."
                ),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
    return unit, source


def _batch_result_payload(result: DeviceIntakeBatchResult) -> dict[str, Any]:
    return {
        "pz_id": result.pz_id,
        "pz_number": result.pz_number,
        "supplier_id": result.supplier_id,
        "items": [
            {
                "model_id": item.model_id,
                "producer": item.producer,
                "model": item.model,
                "warehouse_item_id": item.warehouse_item_id,
                "warehouse_index": item.warehouse_index,
                "zakpozycja_id": item.zakpozycja_id,
                "serial_id": None,
                "serial": item.serial,
                "ewidencja": item.ewidencja,
                "machine_id": item.machine_id,
                "machine_table_id": item.machine_table_id,
                "purchase_price_netto": str(item.purchase_price_netto or Decimal("0")),
                "counter_bw": item.counter_bw,
                "counter_color": item.counter_color,
                "counter_scan": item.counter_scan,
            }
            for item in result.items
        ],
    }


def _validate_exception(payload: DeviceIntakeBatchRequest) -> None:
    requires_exception = not str(payload.external_document or "").strip() or any(
        item.purchase_price_netto <= 0 for item in payload.items
    )
    if requires_exception and not payload.allow_exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Brak dokumentu zewnętrznego lub cena 0 wymaga zaznaczenia wyjątku "
                "i podania uzasadnienia."
            ),
        )
    if payload.allow_exception and len(str(payload.exception_reason or "").strip()) < 10:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uzasadnienie wyjątku musi mieć co najmniej 10 znaków.",
        )


async def _execute_intake_batch(
    payload: DeviceIntakeBatchRequest,
    *,
    admin_session,
    admin_user,
    session: AsyncSession,
) -> dict[str, Any]:
    firebird_user = await _ensure_device_writer(session, admin_user)
    runtime = await _ensure_firebird_write_enabled(session)
    _validate_exception(payload)

    request_payload = payload.model_dump(mode="json")
    request_hash = canonical_request_hash(request_payload)
    try:
        operation, replayed = await begin_intake_operation(
            session,
            idempotency_key=str(payload.idempotency_key),
            request_hash=request_hash,
            request_payload=request_payload,
            created_by=admin_user.id,
            supplier_firebird_id=payload.supplier_id,
            external_document=payload.external_document,
            exception_reason=payload.exception_reason,
        )
    except DeviceIdempotencyConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    if replayed and operation.status == "completed":
        return {
            "ok": True,
            "replayed": True,
            "operation": intake_operation_payload(operation),
            "batch": operation.result_snapshot,
        }

    operation.status = "processing"
    operation.error_text = None
    await session.commit()

    try:
        with use_firebird_runtime_config(runtime):
            result = await asyncio.to_thread(
                create_device_intake_batch,
                items=[
                    DeviceIntakeItemInput(
                        model_id=item.model_id,
                        serial=item.serial,
                        ewidencja=item.ewidencja,
                        purchase_price_netto=item.purchase_price_netto,
                        counter_bw=item.counter_bw,
                        counter_color=item.counter_color,
                        counter_scan=item.counter_scan,
                    )
                    for item in payload.items
                ],
                supplier_id=payload.supplier_id,
                external_document=payload.external_document,
                document_date=payload.document_date,
                issue_date=payload.issue_date,
                payment_method=payload.payment_method,
                payment_due_date=payload.payment_due_date,
                issued_by=firebird_user.login_user,
                ewidencja_prefix=payload.ewidencja_prefix,
                idempotency_key=str(payload.idempotency_key),
                allow_exception=payload.allow_exception,
                exception_reason=payload.exception_reason,
                kto=f"CTIP/{firebird_user.login_user}",
            )
    except ValueError as exc:
        operation = await find_intake_operation(session, str(payload.idempotency_key))
        if operation is not None:
            await mark_intake_operation_failed(session, operation, str(exc))
            await session.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        operation = await find_intake_operation(session, str(payload.idempotency_key))
        if operation is not None:
            await mark_intake_operation_failed(session, operation, str(exc))
            await session.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Błąd tworzenia przyjęcia PZ: {exc}",
        ) from exc

    result_payload = _batch_result_payload(result)
    try:
        operation = await find_intake_operation(session, str(payload.idempotency_key))
        if operation is None:
            raise RuntimeError("Brak rejestru rozpoczętej operacji przyjęcia.")
        await session.refresh(operation)
        if operation.status == "completed":
            return {
                "ok": True,
                "replayed": True,
                "operation": intake_operation_payload(operation),
                "batch": operation.result_snapshot,
                "sheet_sync_status": "pending",
            }
        await complete_intake_operation(session, operation, result_payload)
        await record_audit(
            session,
            user_id=admin_user.id,
            action="device_intake_batch_create",
            client_ip=admin_session.client_ip,
            payload={
                "idempotency_key": str(payload.idempotency_key),
                "pz_id": result.pz_id,
                "pz_number": result.pz_number,
                "document_date": (
                    payload.document_date.isoformat() if payload.document_date else None
                ),
                "issue_date": payload.issue_date.isoformat() if payload.issue_date else None,
                "payment_method": payload.payment_method,
                "payment_due_date": (
                    payload.payment_due_date.isoformat() if payload.payment_due_date else None
                ),
                "supplier_id": result.supplier_id,
                "item_count": len(result.items),
                "exception_used": payload.allow_exception,
                "exception_reason": payload.exception_reason,
                "firebird_user_id": firebird_user.id,
                "firebird_user_login": firebird_user.login_user,
            },
        )
        await session.commit()
    except Exception as exc:
        await session.rollback()
        operation = await find_intake_operation(session, str(payload.idempotency_key))
        if operation is not None:
            await mark_intake_operation_failed(
                session,
                operation,
                f"Firebird zatwierdzony, rejestr CTIP wymaga uzgodnienia: {exc}",
                reconcile_required=True,
            )
            await session.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "PZ zapisano w Firebird, ale rejestr CTIP wymaga uzgodnienia. "
                "Ponów żądanie z tym samym kluczem idempotencji."
            ),
        ) from exc

    return {
        "ok": True,
        "replayed": replayed,
        "message": (
            f"Utworzono przyjęcie {result.pz_number}: "
            f"PZ ID {result.pz_id}, pozycje {len(result.items)}."
        ),
        "operation": intake_operation_payload(operation),
        "batch": result_payload,
        "sheet_sync_status": "pending",
    }


@router.get("/intake/defaults", summary="Pobierz sugestię numeracji KP")
async def device_intake_defaults(
    ewidencja_prefix: str | None = Query(default=None, max_length=50),
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    _, admin_user = admin_context
    await _ensure_device_access(session, admin_user)
    try:
        defaults = await _run_firebird_read(
            session,
            get_next_ewidencja_suggestion,
            prefix=ewidencja_prefix,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Błąd odczytu numeracji KP: {exc}",
        ) from exc
    return {"ok": True, "defaults": defaults}


@router.get("/models", summary="Wyszukaj modele urządzeń")
async def device_models_lookup(
    query: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=100, ge=1, le=500),
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    _, admin_user = admin_context
    await _ensure_device_access(session, admin_user)
    try:
        rows = await _run_firebird_read(
            session,
            search_device_models,
            query=query,
            limit=limit,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Błąd odczytu listy modeli: {exc}",
        ) from exc
    return {"ok": True, "rows": rows}


@router.get("/model-form-options", summary="Słowniki formularza modelu")
async def device_model_form_options(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    _, admin_user = admin_context
    await _ensure_device_access(session, admin_user)
    try:
        brands = await _get_or_seed_device_brands(session)
        taxonomy = await _run_firebird_read(session, load_device_model_taxonomy)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Błąd odczytu słowników modelu: {exc}",
        ) from exc
    return {
        "ok": True,
        "options": {
            "brands": brands,
            "default_brand": "Ricoh",
            "groups": taxonomy.get("groups", []),
            "kinds": taxonomy.get("kinds", []),
        },
    }


@router.post("/models", summary="Dodaj kompletny model urządzenia")
async def device_model_create(
    payload: DeviceModelCreateRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    admin_session, admin_user = admin_context
    firebird_user = await _ensure_device_writer(session, admin_user)
    runtime = await _ensure_firebird_write_enabled(session)
    try:
        with use_firebird_runtime_config(runtime):
            result = await asyncio.to_thread(
                create_device_model,
                marka=payload.marka,
                model_name=payload.model,
                grupa=payload.grupa,
                rodzaj=payload.rodzaj,
                kolor=payload.kolor,
                plik=payload.plik,
                kto=f"CTIP/{firebird_user.login_user}",
            )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Błąd tworzenia modelu: {exc}",
        ) from exc
    await record_audit(
        session,
        user_id=admin_user.id,
        action="device_model_create",
        client_ip=admin_session.client_ip,
        payload=result,
    )
    await session.commit()
    return {"ok": True, "model": result}


@router.get("/suppliers", summary="Wyszukaj dostawców")
async def device_suppliers_lookup(
    query: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=100, ge=1, le=500),
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    _, admin_user = admin_context
    await _ensure_device_access(session, admin_user)
    try:
        rows = await _run_firebird_read(
            session,
            search_device_suppliers,
            query=query,
            limit=limit,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Błąd odczytu dostawców: {exc}",
        ) from exc
    return {"ok": True, "rows": rows}


@router.post("/suppliers", summary="Dodaj dostawcę")
async def device_supplier_create(
    payload: DeviceSupplierCreateRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    admin_session, admin_user = admin_context
    firebird_user = await _ensure_device_writer(session, admin_user)
    runtime = await _ensure_firebird_write_enabled(session)
    try:
        with use_firebird_runtime_config(runtime):
            supplier = await asyncio.to_thread(
                create_device_supplier,
                name=payload.name,
                nip=payload.nip,
                address=payload.address,
                postal_code=payload.postal_code,
                city=payload.city,
                phone=payload.phone,
                email=payload.email,
                kto=f"CTIP/{firebird_user.login_user}",
            )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Błąd tworzenia dostawcy: {exc}",
        ) from exc
    await record_audit(
        session,
        user_id=admin_user.id,
        action="device_supplier_create",
        client_ip=admin_session.client_ip,
        payload=supplier,
    )
    await session.commit()
    return {"ok": True, "supplier": supplier}


@router.get("/dashboard", summary="Podsumowanie obsługi urządzeń")
async def device_dashboard_data(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    _, admin_user = admin_context
    await _ensure_device_access(session, admin_user)
    try:
        return await _run_firebird_read(session, load_device_dashboard_payload)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Błąd odczytu dashboardu urządzeń: {exc}",
        ) from exc


@router.get("/bnp-buyout/lookup", summary="Wyszukaj urządzenie do wykupu BNP")
async def device_bnp_buyout_lookup(
    serial: str = Query(min_length=1, max_length=100),
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Zwraca urządzenie, klienta i kartoteki magazynowe dla wykupu BNP."""
    _, admin_user = admin_context
    await _ensure_device_access(session, admin_user)
    try:
        lookup = await _run_firebird_read(
            session,
            lookup_bnp_buyout,
            serial=serial,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Błąd wyszukiwania urządzenia do wykupu BNP: {exc}",
        ) from exc
    return {"ok": True, "lookup": lookup}


@router.post("/bnp-buyout/catalog", summary="Utwórz kartotekę wykupu BNP")
async def device_bnp_buyout_catalog_create(
    payload: DeviceBnpCatalogCreateRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Tworzy kartotekę urządzenia na magazynie 27 ze stanem 0."""
    admin_session, admin_user = admin_context
    firebird_user = await _ensure_device_writer(session, admin_user)
    runtime = await _ensure_firebird_write_enabled(session)
    try:
        with use_firebird_runtime_config(runtime):
            result = await asyncio.to_thread(
                create_bnp_catalog_item,
                serial=payload.serial,
                machine_table_id=payload.machine_table_id,
                expected_ewidencja=payload.expected_ewidencja,
                warehouse_index=payload.warehouse_index,
                item_name=payload.item_name,
                kto=f"CTIP/{firebird_user.login_user}",
            )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Błąd tworzenia kartoteki wykupu BNP: {exc}",
        ) from exc

    await record_audit(
        session,
        user_id=admin_user.id,
        action="device_bnp_catalog_create",
        client_ip=admin_session.client_ip,
        payload={
            "created": result.created,
            "serial": payload.serial,
            "machine_table_id": payload.machine_table_id,
            "warehouse_item_id": result.warehouse_item["id_magazyn_table"],
            "warehouse_index": result.warehouse_item["index"],
        },
    )
    await session.commit()
    return {
        "ok": True,
        "message": (
            "Utworzono kartotekę wykupu BNP ze stanem 0."
            if result.created
            else "Kartoteka wykupu BNP już istnieje."
        ),
        "created": result.created,
        "warehouse_item": result.warehouse_item,
    }


@router.post("/bnp-buyout/complete", summary="Zatwierdź wykup BNP")
async def device_bnp_buyout_complete(
    payload: DeviceBnpBuyoutCompleteRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Zmienia KP na WKP i tworzy PZ wykupu na magazynie 27."""
    admin_session, admin_user = admin_context
    firebird_user = await _ensure_device_writer(session, admin_user)
    runtime = await _ensure_firebird_write_enabled(session)
    try:
        with use_firebird_runtime_config(runtime):
            result = await asyncio.to_thread(
                complete_bnp_buyout,
                serial=payload.serial,
                machine_table_id=payload.machine_table_id,
                warehouse_item_id=payload.warehouse_item_id,
                expected_ewidencja=payload.expected_ewidencja,
                target_ewidencja=payload.target_ewidencja,
                warehouse_index=payload.warehouse_index,
                item_name=payload.item_name,
                external_document=payload.external_document,
                document_date=payload.document_date,
                purchase_price_netto=payload.purchase_price_netto,
                issued_by=firebird_user.login_user,
                kto=f"CTIP/{firebird_user.login_user}",
            )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Błąd finalizacji wykupu BNP: {exc}",
        ) from exc

    await record_audit(
        session,
        user_id=admin_user.id,
        action="device_bnp_buyout_complete",
        client_ip=admin_session.client_ip,
        payload={
            "already_completed": result.already_completed,
            "serial": payload.serial,
            "machine_id": result.machine_id,
            "machine_table_id": result.machine_table_id,
            "previous_ewidencja": result.previous_ewidencja,
            "target_ewidencja": result.target_ewidencja,
            "warehouse_item_id": result.warehouse_item_id,
            "warehouse_index": result.warehouse_index,
            "pz_id": result.pz_id,
            "pz_number": result.pz_number,
            "zakpozycja_id": result.zakpozycja_id,
            "external_document": result.external_document,
            "purchase_price_netto": float(result.purchase_price_netto),
        },
    )
    await session.commit()
    return {
        "ok": True,
        "message": (
            f"Wykup BNP był już zapisany jako {result.pz_number}."
            if result.already_completed
            else f"Zapisano wykup BNP jako {result.pz_number}."
        ),
        "buyout": {
            "already_completed": result.already_completed,
            "pz_id": result.pz_id,
            "pz_number": result.pz_number,
            "zakpozycja_id": result.zakpozycja_id,
            "warehouse_item_id": result.warehouse_item_id,
            "warehouse_index": result.warehouse_index,
            "warehouse_quantity": float(result.warehouse_quantity),
            "machine_id": result.machine_id,
            "machine_table_id": result.machine_table_id,
            "previous_ewidencja": result.previous_ewidencja,
            "target_ewidencja": result.target_ewidencja,
            "supplier_id": result.supplier_id,
            "external_document": result.external_document,
            "purchase_price_netto": float(result.purchase_price_netto),
        },
    }


def _serialize_device_audit_run(run: DeviceAuditRun) -> dict[str, Any]:
    """Serializuje przebieg audytu do odpowiedzi API."""
    return {
        "id": run.id,
        "status": run.status,
        "requested_by": run.requested_by,
        "phase": run.phase,
        "processed_items": run.processed_items,
        "total_items": run.total_items,
        "summary": run.summary or {},
        "source_snapshot": run.source_snapshot or {},
        "error_text": run.error_text,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }


def _serialize_device_audit_item(item: DeviceAuditItem) -> dict[str, Any]:
    """Serializuje szczegół pojedynczego wyniku audytu."""
    return {
        "id": item.id,
        "canonical_key": item.canonical_key,
        "producer": item.producer,
        "model": item.model,
        "serial": item.serial,
        "ewidencja": item.ewidencja,
        "source_row": item.source_row,
        "sheet_row": item.sheet_row,
        "machine_id": item.machine_id,
        "ctip_unit_id": item.ctip_unit_id,
        "source_presence": {
            "sheet": item.sheet_present,
            "warehouse": item.warehouse_present,
            "machine": item.machine_present,
            "ctip": item.ctip_present,
        },
        "result_status": item.result_status,
        "issue_codes": item.issue_codes or [],
        "issue_summary": item.issue_summary,
        "source_details": item.source_details or {},
    }


@router.post(
    "/audits",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Uruchom audyt spójności urządzeń",
)
async def device_audit_create(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Kolejkuje jeden ręczny audyt; audyt nigdy nie modyfikuje źródeł."""
    admin_session, admin_user = admin_context
    await _ensure_device_access(session, admin_user)
    async with _DEVICE_AUDIT_CREATE_LOCK:
        active_run = (
            (
                await session.execute(
                    select(DeviceAuditRun)
                    .where(DeviceAuditRun.status.in_(("pending", "running")))
                    .order_by(DeviceAuditRun.created_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if active_run is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Audyt urządzeń jest już uruchomiony.",
            )
        run = DeviceAuditRun(
            status="pending",
            requested_by=admin_user.id,
            phase="Oczekiwanie na worker",
            processed_items=0,
            total_items=0,
        )
        session.add(run)
        await session.flush()
        await record_audit(
            session,
            user_id=admin_user.id,
            action="device_audit_requested",
            client_ip=admin_session.client_ip,
            payload={"run_id": run.id, "read_only": True},
        )
        await session.commit()
    return {"ok": True, "run": _serialize_device_audit_run(run)}


@router.get("/audits", summary="Historia audytów urządzeń")
async def device_audit_history(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Zwraca maksymalnie 20 ostatnich przebiegów audytu."""
    _, admin_user = admin_context
    await _ensure_device_access(session, admin_user)
    runs = list(
        (
            await session.execute(
                select(DeviceAuditRun)
                .order_by(DeviceAuditRun.created_at.desc(), DeviceAuditRun.id.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    return {"ok": True, "items": [_serialize_device_audit_run(run) for run in runs]}


@router.get("/audits/latest", summary="Ostatni zakończony audyt urządzeń")
async def device_audit_latest(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Zwraca ostatni poprawnie zakończony audyt."""
    _, admin_user = admin_context
    await _ensure_device_access(session, admin_user)
    run = (
        (
            await session.execute(
                select(DeviceAuditRun)
                .where(DeviceAuditRun.status == "completed")
                .order_by(DeviceAuditRun.completed_at.desc(), DeviceAuditRun.id.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    return {"ok": True, "run": _serialize_device_audit_run(run) if run else None}


@router.get("/audits/{run_id}", summary="Szczegóły audytu urządzeń")
async def device_audit_detail(
    run_id: int,
    query: str | None = Query(default=None, max_length=200),
    result: str | None = Query(default=None, max_length=20),
    source: str = Query(default="operational", max_length=20),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=10, le=200),
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Zwraca stronicowane, filtrowalne wyniki wskazanego przebiegu."""
    _, admin_user = admin_context
    await _ensure_device_access(session, admin_user)
    run = await session.get(DeviceAuditRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nie znaleziono audytu.")
    base_conditions = [DeviceAuditItem.run_id == run_id]
    normalized_source = str(source or "operational").strip().lower()
    source_conditions = {
        "operational": or_(
            DeviceAuditItem.sheet_present.is_(True),
            DeviceAuditItem.warehouse_present.is_(True),
        ),
        "sheet": DeviceAuditItem.sheet_present.is_(True),
        "warehouse": DeviceAuditItem.warehouse_present.is_(True),
        "machine": or_(
            DeviceAuditItem.machine_present.is_(True),
            cast(DeviceAuditItem.issue_codes, Text).ilike("%DUPLICATE_MACHINE%"),
        ),
        "ctip": or_(
            DeviceAuditItem.ctip_present.is_(True),
            cast(DeviceAuditItem.issue_codes, Text).ilike("%DUPLICATE_CTIP%"),
        ),
    }
    if normalized_source != "all":
        source_condition = source_conditions.get(normalized_source)
        if source_condition is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Nieprawidłowy filtr źródła audytu.",
            )
        base_conditions.append(source_condition)
    normalized_query = str(query or "").strip()
    if normalized_query:
        pattern = f"%{normalized_query}%"
        base_conditions.append(
            or_(
                DeviceAuditItem.producer.ilike(pattern),
                DeviceAuditItem.model.ilike(pattern),
                DeviceAuditItem.serial.ilike(pattern),
                DeviceAuditItem.ewidencja.ilike(pattern),
                DeviceAuditItem.issue_summary.ilike(pattern),
            )
        )
    summary_rows = (
        await session.execute(
            select(
                DeviceAuditItem.result_status,
                func.count(DeviceAuditItem.id),
            )
            .where(*base_conditions)
            .group_by(DeviceAuditItem.result_status)
        )
    ).all()
    filtered_summary = {
        "total": sum(int(count) for _, count in summary_rows),
        "ok": 0,
        "missing": 0,
        "discrepancy": 0,
        "duplicate": 0,
    }
    for result_status, count in summary_rows:
        filtered_summary[str(result_status)] = int(count)

    conditions = list(base_conditions)
    normalized_result = str(result or "").strip().lower()
    if normalized_result and normalized_result != "all":
        if normalized_result not in {"ok", "missing", "discrepancy", "duplicate"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Nieprawidłowy filtr wyniku audytu.",
            )
        conditions.append(DeviceAuditItem.result_status == normalized_result)
    total = int(
        (
            await session.execute(select(func.count(DeviceAuditItem.id)).where(*conditions))
        ).scalar_one()
    )
    pages = (total + page_size - 1) // page_size
    safe_page = min(page, pages or 1)
    items = list(
        (
            await session.execute(
                select(DeviceAuditItem)
                .where(*conditions)
                .order_by(
                    DeviceAuditItem.result_status.desc(),
                    DeviceAuditItem.producer,
                    DeviceAuditItem.model,
                    DeviceAuditItem.id,
                )
                .offset((safe_page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return {
        "ok": True,
        "run": _serialize_device_audit_run(run),
        "source": normalized_source,
        "filtered_summary": filtered_summary,
        "items": [_serialize_device_audit_item(item) for item in items],
        "total": total,
        "page": safe_page,
        "page_size": page_size,
        "pages": pages,
    }


@router.get("/warehouse", summary="Scalony stan magazynu urządzeń")
async def device_warehouse_list(
    query: str | None = Query(default=None, max_length=200),
    reservation: str | None = Query(default=None, max_length=20),
    sheet_sync: str | None = Query(default=None, max_length=30),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=10, le=200),
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Zwraca magazyn Firebird wzbogacony o rezerwacje, uwagi i stan arkusza."""
    _, admin_user = admin_context
    await _ensure_device_access(session, admin_user)
    try:
        firebird_rows = await _run_firebird_read(
            session,
            load_available_devices_from_firebird_warehouse,
            limit=2000,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Błąd odczytu magazynu urządzeń: {exc}",
        ) from exc
    payload = await build_device_warehouse_payload(
        session,
        firebird_rows=firebird_rows,
        query=query,
        reservation_filter=reservation,
        sheet_filter=sheet_sync,
        page=page,
        page_size=page_size,
    )
    return {"ok": True, **payload}


@router.get("/warehouse/{source_row}", summary="Szczegóły egzemplarza magazynowego")
async def device_warehouse_detail(
    source_row: int,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Zwraca aktualny stan i historię pojedynczego egzemplarza."""
    _, admin_user = admin_context
    await _ensure_device_access(session, admin_user)
    source = await _load_available_warehouse_row(session, source_row)
    payload = await build_device_warehouse_payload(
        session,
        firebird_rows=[source],
        page=1,
        page_size=10,
    )
    if not payload["items"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Nie znaleziono egzemplarza.",
        )
    item = payload["items"][0]
    events = (
        await serialize_device_events(session, unit_id=int(item["unit_id"]))
        if item.get("unit_id")
        else []
    )
    return {"ok": True, "item": item, "events": events}


@router.post("/warehouse/{source_row}/counters", summary="Zapisz liczniki urządzenia")
async def device_warehouse_counters_save(
    source_row: int,
    payload: DeviceCounterReadingRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Zapisuje odczyt historyczny albo aktualizuje bieżące liczniki urządzenia."""
    admin_session, admin_user = admin_context
    await _ensure_device_writer(session, admin_user)
    values = (payload.counter_bw, payload.counter_color, payload.counter_scan)
    if not any(value is not None for value in values):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Podaj co najmniej jeden licznik.",
        )
    unit, _ = await _ensure_registered_warehouse_unit(session, source_row=source_row)
    reading_at = payload.reading_at
    if reading_at.tzinfo is None:
        reading_at = reading_at.replace(tzinfo=ZoneInfo("Europe/Warsaw"))
    latest = (
        (
            await session.execute(
                select(DeviceCounterReading)
                .where(DeviceCounterReading.unit_id == unit.id)
                .order_by(DeviceCounterReading.reading_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    historical = latest is not None and reading_at < latest.reading_at
    lower_fields = []
    if latest is not None and not historical:
        for label, new_value, old_value in (
            ("B/W", payload.counter_bw, latest.counter_bw),
            ("kolor", payload.counter_color, latest.counter_color),
            ("skan", payload.counter_scan, latest.counter_scan),
        ):
            if new_value is not None and old_value is not None and new_value < old_value:
                lower_fields.append(label)
    reason = str(payload.override_reason or "").strip()
    if lower_fields and (not payload.allow_lower or len(reason) < 10):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Niższy odczyt ({', '.join(lower_fields)}) wymaga potwierdzenia "
                "i uzasadnienia mającego co najmniej 10 znaków."
            ),
        )

    applied = not historical
    saved_state: dict[str, Any] | None = None
    if applied:
        if unit.firebird_machine_table_id is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Urządzenie nie ma powiązania z kartoteką MASZYNA.",
            )
        runtime = await _ensure_firebird_write_enabled(session)
        try:
            with use_firebird_runtime_config(runtime):
                saved_state = await asyncio.to_thread(
                    update_device_machine_counters,
                    machine_table_id=unit.firebird_machine_table_id,
                    counter_bw=payload.counter_bw,
                    counter_color=payload.counter_color,
                    counter_scan=payload.counter_scan,
                )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    reading = DeviceCounterReading(
        unit_id=unit.id,
        source="manual",
        reading_at=reading_at,
        counter_bw=payload.counter_bw,
        counter_color=payload.counter_color,
        counter_scan=payload.counter_scan,
        applied_to_current=applied,
        override_reason=reason or None,
        note=str(payload.note or "").strip() or None,
        created_by=admin_user.id,
        source_snapshot=saved_state,
    )
    session.add(reading)
    await session.flush()
    session.add(
        DeviceInventoryEvent(
            unit_id=unit.id,
            event_type="counter_reading_saved",
            created_by=admin_user.id,
            payload={
                "reading_id": reading.id,
                "reading_at": reading_at.isoformat(),
                "applied_to_current": applied,
                "counter_bw": payload.counter_bw,
                "counter_color": payload.counter_color,
                "counter_scan": payload.counter_scan,
            },
        )
    )
    if applied:
        await enqueue_sheet_operation(
            session,
            unit=unit,
            operation_type="update_counters",
            payload={
                "source_row": unit.source_row,
                "serial": unit.serial,
                "ewidencja": unit.ewidencja,
                "counter_bw": payload.counter_bw,
                "counter_color": payload.counter_color,
                "counter_scan": payload.counter_scan,
                "ctip_env": settings.ctip_runtime_profile.upper(),
            },
            idempotency_key=f"counter:{reading.id}:{unit.id}",
        )
    await record_audit(
        session,
        user_id=admin_user.id,
        action="device_counter_reading_save",
        client_ip=admin_session.client_ip,
        payload={
            "unit_id": unit.id,
            "reading_id": reading.id,
            "applied_to_current": applied,
        },
    )
    await session.commit()
    return {
        "ok": True,
        "reading_id": reading.id,
        "applied_to_current": applied,
        "message": (
            "Zapisano bieżące liczniki urządzenia."
            if applied
            else "Zapisano odczyt historyczny bez zmiany bieżących liczników."
        ),
    }


@router.post("/warehouse/{source_row}/notes", summary="Zapisz uwagę urządzenia")
async def device_warehouse_note_save(
    source_row: int,
    payload: DeviceNoteRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Wersjonuje uwagę w CTIP i kolejkuje jej publikację w arkuszu."""
    admin_session, admin_user = admin_context
    await _ensure_device_access(session, admin_user)
    unit, _ = await _ensure_registered_warehouse_unit(
        session,
        source_row=source_row,
    )
    try:
        event = await add_device_note(
            session,
            unit=unit,
            user_id=admin_user.id,
            note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await record_audit(
        session,
        user_id=admin_user.id,
        action="device_note_change",
        client_ip=admin_session.client_ip,
        payload={"source_row": source_row, "unit_id": unit.id, "event_id": event.id},
    )
    await session.commit()
    return {
        "ok": True,
        "message": "Zapisano uwagę; synchronizacja arkusza oczekuje w kolejce.",
        "unit_id": unit.id,
        "event_id": event.id,
        "sheet_sync_status": unit.sheet_sync_status,
    }


@router.put("/warehouse/{source_row}/reservation", summary="Zapisz rezerwację ręczną")
async def device_warehouse_reservation_save(
    source_row: int,
    payload: DeviceReservationRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Tworzy terminową rezerwację, o ile urządzenie nie jest zablokowane przez FLOW."""
    admin_session, admin_user = admin_context
    await _ensure_device_access(session, admin_user)
    unit, _ = await _ensure_registered_warehouse_unit(
        session,
        source_row=source_row,
    )
    expires_at = payload.expires_at
    if expires_at is None:
        local_now = datetime.now(ZoneInfo("Europe/Warsaw"))
        expires_at = local_now + timedelta(
            days=max(1, int(settings.device_manual_reservation_default_days))
        )
    elif expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=ZoneInfo("Europe/Warsaw"))
    expires_at = expires_at.astimezone(UTC)
    try:
        reservation = await save_manual_reservation(
            session,
            unit=unit,
            user_id=admin_user.id,
            reserved_for=payload.reserved_for,
            reason=payload.reason,
            expires_at=expires_at,
        )
    except DeviceReservationConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await record_audit(
        session,
        user_id=admin_user.id,
        action="device_manual_reservation_save",
        client_ip=admin_session.client_ip,
        payload={
            "source_row": source_row,
            "unit_id": unit.id,
            "reservation_id": reservation.id,
            "reserved_for": reservation.reserved_for,
            "expires_at": reservation.expires_at.isoformat(),
            "reason": reservation.reason,
        },
    )
    await session.commit()
    return {
        "ok": True,
        "message": "Zapisano rezerwację ręczną.",
        "reservation": {
            "id": reservation.id,
            "reserved_for": reservation.reserved_for,
            "reason": reservation.reason,
            "expires_at": reservation.expires_at.isoformat(),
        },
    }


@router.delete("/warehouse/{source_row}/reservation", summary="Zwolnij rezerwację ręczną")
async def device_warehouse_reservation_release(
    source_row: int,
    payload: DeviceReservationReleaseRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Zwalnia rezerwację ręczną z wymaganym uzasadnieniem."""
    admin_session, admin_user = admin_context
    await _ensure_device_access(session, admin_user)
    unit = await find_unit_by_source_or_identity(session, source_row=source_row)
    if unit is None or await get_active_manual_reservation(session, unit_id=unit.id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Egzemplarz nie ma aktywnej rezerwacji ręcznej.",
        )
    try:
        reservation = await release_manual_reservation(
            session,
            unit=unit,
            user_id=admin_user.id,
            reason=payload.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await record_audit(
        session,
        user_id=admin_user.id,
        action="device_manual_reservation_release",
        client_ip=admin_session.client_ip,
        payload={
            "source_row": source_row,
            "unit_id": unit.id,
            "reservation_id": reservation.id,
            "reason": payload.reason,
        },
    )
    await session.commit()
    return {"ok": True, "message": "Zwolniono rezerwację ręczną."}


@router.get("/history", summary="Historia przyjęć urządzeń")
async def device_intake_history(
    limit: int = Query(default=100, ge=1, le=500),
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Zwraca operacje PZ bez automatycznego poprawiania danych historycznych."""
    _, admin_user = admin_context
    await _ensure_device_access(session, admin_user)
    rows = list(
        (
            await session.execute(
                select(DeviceIntakeOperation)
                .order_by(
                    DeviceIntakeOperation.created_at.desc(),
                    DeviceIntakeOperation.id.desc(),
                )
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return {
        "ok": True,
        "can_withdraw": admin_user.role == "admin" or bool(admin_user.can_withdraw_device_pz),
        "items": [intake_operation_payload(row) for row in rows],
    }


async def _load_withdrawable_operation(
    session: AsyncSession, operation_id: int
) -> DeviceIntakeOperation:
    operation = await session.get(DeviceIntakeOperation, operation_id)
    if operation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nie znaleziono PZ.")
    if operation.firebird_pz_id is None or not operation.result_snapshot:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Operacja nie ma pełnego zapisu PZ wymaganego do wycofania.",
        )
    return operation


@router.get("/history/{operation_id}/withdrawal-preview", summary="Podgląd wycofania PZ")
async def device_pz_withdrawal_preview(
    operation_id: int,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Sprawdza PZ, utworzone elementy i późniejsze zależności bez zapisu."""
    _, admin_user = admin_context
    await _ensure_pz_withdrawal_permission(session, admin_user)
    operation = await _load_withdrawable_operation(session, operation_id)
    if operation.status == "withdrawn":
        return {
            "ok": True,
            "already_withdrawn": True,
            "preview": operation.withdrawal_preview or {},
        }
    runtime = await load_firebird_runtime_config(session)
    with use_firebird_runtime_config(runtime):
        preview = await asyncio.to_thread(
            preview_device_pz_withdrawal,
            pz_id=operation.firebird_pz_id,
            expected=operation.result_snapshot,
        )
    return {
        "ok": True,
        "already_withdrawn": False,
        "can_force": admin_user.role == "admin",
        "preview": preview,
    }


@router.delete("/history/{operation_id}", summary="Wycofaj dokument PZ")
async def device_pz_withdraw(
    operation_id: int,
    payload: DevicePzWithdrawalRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Wycofuje PZ po ponownej kontroli Firebird i zachowuje historię CTIP."""
    admin_session, admin_user = admin_context
    await _ensure_pz_withdrawal_permission(session, admin_user)
    operation = await _load_withdrawable_operation(session, operation_id)
    if operation.status == "withdrawn":
        return {"ok": True, "replayed": True, "message": "Dokument PZ jest już wycofany."}
    if payload.force and admin_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tylko administrator może wymusić wycofanie zmienionego PZ.",
        )
    if payload.confirmation.strip() != str(operation.firebird_pz_number or "").strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Wpisany numer PZ nie zgadza się z wycofywanym dokumentem.",
        )
    runtime = await _ensure_firebird_write_enabled(session)
    try:
        with use_firebird_runtime_config(runtime):
            preview = await asyncio.to_thread(
                withdraw_device_pz,
                pz_id=operation.firebird_pz_id,
                expected=operation.result_snapshot,
                force=payload.force,
            )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    now = datetime.now(UTC)
    units = list(
        (
            await session.execute(
                select(DeviceInventoryUnit).where(DeviceInventoryUnit.operation_id == operation.id)
            )
        )
        .scalars()
        .all()
    )
    for unit in units:
        await enqueue_sheet_operation(
            session,
            unit=unit,
            operation_type="delete_device",
            payload={
                "source_row": unit.source_row,
                "serial": unit.serial,
                "ewidencja": unit.ewidencja,
                "ctip_env": settings.ctip_runtime_profile.upper(),
            },
            idempotency_key=f"withdraw:{operation.id}:{unit.id}:sheet",
        )
        unit.status = "withdrawn"
        unit.withdrawn_at = now
        session.add(
            DeviceInventoryEvent(
                unit_id=unit.id,
                event_type="intake_withdrawn",
                created_by=admin_user.id,
                payload={"operation_id": operation.id, "reason": payload.reason},
            )
        )
    operation.status = "withdrawn"
    operation.withdrawn_by = admin_user.id
    operation.withdrawal_reason = payload.reason.strip()
    operation.withdrawal_preview = preview
    operation.withdrawn_at = now
    operation.updated_at = now
    await record_audit(
        session,
        user_id=admin_user.id,
        action="device_pz_withdraw",
        client_ip=admin_session.client_ip,
        payload={
            "operation_id": operation.id,
            "pz_id": operation.firebird_pz_id,
            "pz_number": operation.firebird_pz_number,
            "force": payload.force,
            "reason": payload.reason,
        },
    )
    await session.commit()
    return {
        "ok": True,
        "replayed": False,
        "message": (
            f"Wycofano {operation.firebird_pz_number}. "
            "Usunięcie wierszy arkusza dodano do kolejki."
        ),
        "preview": preview,
    }


@router.get("/issues", summary="Problemy wymagające uzgodnienia")
async def device_issues(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Zwraca nieudane operacje PZ i zadania arkusza po wyczerpaniu prób."""
    _, admin_user = admin_context
    await _ensure_device_access(session, admin_user)
    operations = list(
        (
            await session.execute(
                select(DeviceIntakeOperation)
                .where(DeviceIntakeOperation.status.in_(("failed", "reconcile_required")))
                .order_by(DeviceIntakeOperation.updated_at.desc())
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    outbox = list(
        (
            await session.execute(
                select(DeviceSheetOutbox)
                .where(DeviceSheetOutbox.status == "failed")
                .order_by(DeviceSheetOutbox.updated_at.desc())
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    recent_outbox = list(
        (
            await session.execute(
                select(DeviceSheetOutbox).order_by(DeviceSheetOutbox.created_at.desc()).limit(300)
            )
        )
        .scalars()
        .all()
    )
    recent_events = list(
        (
            await session.execute(
                select(DeviceInventoryEvent)
                .order_by(DeviceInventoryEvent.created_at.desc())
                .limit(300)
            )
        )
        .scalars()
        .all()
    )

    def serialize_outbox(row) -> dict[str, Any]:
        return {
            "id": row.id,
            "unit_id": row.unit_id,
            "operation_type": row.operation_type,
            "status": row.status,
            "attempt_count": row.attempt_count,
            "max_attempts": row.max_attempts,
            "last_error": row.last_error,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        }

    return {
        "ok": True,
        "operations": [intake_operation_payload(row) for row in operations],
        "sheet_outbox": [serialize_outbox(row) for row in outbox],
        "recent_outbox": [serialize_outbox(row) for row in recent_outbox],
        "recent_events": [
            {
                "id": row.id,
                "unit_id": row.unit_id,
                "event_type": row.event_type,
                "payload": row.payload,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in recent_events
        ],
    }


@router.post("/sheet-outbox/{queue_item_id}/retry", summary="Ponów zadanie arkusza")
async def device_sheet_outbox_retry(
    queue_item_id: int,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Przywraca wskazane zadanie synchronizacji do kolejki."""
    admin_session, admin_user = admin_context
    await _ensure_device_access(session, admin_user)
    queue_item = await retry_device_sheet_outbox(queue_item_id)
    if queue_item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Nie znaleziono zadania synchronizacji.",
        )
    await record_audit(
        session,
        user_id=admin_user.id,
        action="device_sheet_outbox_retry",
        client_ip=admin_session.client_ip,
        payload={"queue_item_id": queue_item_id, "status": queue_item.status},
    )
    await session.commit()
    return {"ok": True, "message": "Zadanie przywrócono do kolejki."}


@router.post("/catalog/sync", summary="Wyłączona synchronizacja kartotek AUTO")
async def device_catalog_sync(
    payload: DeviceCatalogSyncRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    del payload
    _, admin_user = admin_context
    await _ensure_device_access(session, admin_user)
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail=(
            "Wspólne kartoteki AUTO zostały wyłączone. "
            "Każdy egzemplarz należy przyjąć dokumentem PZ w /device/intake."
        ),
    )


@router.post("/intake", summary="Utwórz przyjęcie pojedynczego urządzenia")
async def device_intake_create(
    payload: DeviceIntakeRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Zgodnościowy wrapper korzystający z kanonicznego zapisu batch."""
    admin_session, admin_user = admin_context
    batch_payload = DeviceIntakeBatchRequest(
        idempotency_key=payload.idempotency_key,
        supplier_id=payload.supplier_id,
        external_document=payload.external_document,
        document_date=payload.document_date,
        issue_date=payload.issue_date,
        payment_method=payload.payment_method,
        payment_due_date=payload.payment_due_date,
        allow_exception=payload.allow_exception,
        exception_reason=payload.exception_reason,
        ewidencja_prefix=payload.ewidencja_prefix,
        items=[
            DeviceIntakeBatchItemRequest(
                model_id=payload.model_id,
                serial=payload.serial,
                ewidencja=payload.ewidencja,
                purchase_price_netto=payload.purchase_price_netto,
            )
        ],
    )
    response = await _execute_intake_batch(
        batch_payload,
        admin_session=admin_session,
        admin_user=admin_user,
        session=session,
    )
    response["deprecated"] = True
    return response


@router.post("/intake/batch", summary="Utwórz przyjęcie PZ urządzeń")
async def device_intake_batch_create(
    payload: DeviceIntakeBatchRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    admin_session, admin_user = admin_context
    return await _execute_intake_batch(
        payload,
        admin_session=admin_session,
        admin_user=admin_user,
        session=session,
    )


@router.get("/intake/operations/{idempotency_key}", summary="Status operacji przyjęcia")
async def device_intake_operation_status(
    idempotency_key: UUID,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    _, admin_user = admin_context
    await _ensure_device_access(session, admin_user)
    operation = await find_intake_operation(session, str(idempotency_key))
    if operation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Nie znaleziono operacji przyjęcia.",
        )
    return {"ok": True, "operation": intake_operation_payload(operation)}


__all__ = ["router"]
