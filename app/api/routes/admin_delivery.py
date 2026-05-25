"""Endpointy modułu obsługi dostaw."""

from __future__ import annotations

import asyncio
import mimetypes
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_session_context, get_db_session
from app.models import (
    AdminAuditLog,
    DeliveryCase,
    DeliveryCaseDevice,
    DeliveryCaseFile,
    DeliveryCaseTask,
)
from app.services import section_permissions
from app.services.contracts_dashboard import (
    create_client_from_submitted_payload,
    find_client_in_firebird_by_id,
    load_available_devices_from_firebird_warehouse,
    load_client_devices_from_firebird,
    load_firebird_runtime_config,
    normalize_nip,
    search_clients_in_firebird,
    use_firebird_runtime_config,
)
from app.services.contracts_workflow import (
    build_workflow_device_key,
    normalize_workflow_device_source_type,
)
from app.services.delivery import (
    CONTRACT_END_CANCELLED,
    DELIVERY_CASE_TYPE_DELIVERY,
    DELIVERY_CASE_TYPE_PICKUP,
    DELIVERY_DEVICE_ROLE_DELIVERY,
    DELIVERY_DEVICE_ROLE_PICKUP,
    DELIVERY_SOURCE_MANUAL,
    apply_sheet_sync_snapshot_to_delivery_devices,
    archived_contract_files_from_snapshot,
    build_delivery_sheet_payload,
    build_document_context,
    confirm_grenke_contract_end,
    delivery_files_root,
    list_active_device_reservations,
    list_delivery_cases,
    list_document_templates,
    list_grenke_contract_ends,
    load_delivery_case,
    load_delivery_file,
    load_delivery_task,
    load_grenke_contract_end,
    render_docx_template,
    resolve_document_template_path,
    send_grenke_contract_end_reminders,
    serialize_delivery_case,
    serialize_delivery_file,
    serialize_delivery_task,
    serialize_grenke_contract_end,
)
from app.services.workflow_sheet_sync import (
    load_workflow_sheet_runtime_config,
    release_workflow_devices_from_sheet,
    sync_workflow_devices_to_sheet,
    use_workflow_sheet_runtime_config,
)

router = APIRouter(prefix="/admin/delivery", tags=["delivery"])
DELIVERY_UPLOAD_FILE = File(...)


class DeliveryDevicePayload(BaseModel):
    """Urządzenie dodawane do sprawy dostawy albo odbioru."""

    source_type: str | None = Field(default="firebird_magazyn_28", max_length=80)
    source_row: int | None = Field(default=None, ge=1)
    row: int | None = Field(default=None, ge=1)
    producer: str | None = Field(default=None, max_length=120)
    model: str | None = Field(default=None, max_length=220)
    serial: str | None = Field(default=None, max_length=120)
    serial2: str | None = Field(default=None, max_length=120)
    ewidencja: str | None = Field(default=None, max_length=120)
    firebird_machine_id: int | None = Field(default=None, ge=1)
    ms_id_maszyna: int | None = Field(default=None, ge=1)
    ms_id_magazyn_table: int | None = Field(default=None, ge=1)
    id_maszyna: int | None = Field(default=None, ge=1)
    id_maszyna_table: int | None = Field(default=None, ge=1)
    snapshot: dict[str, Any] | None = None

    @field_validator("source_type", "producer", "model", "serial", "serial2", "ewidencja")
    @classmethod
    def _strip_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class ManualDeliveryCreateRequest(BaseModel):
    """Żądanie utworzenia ręcznej sprawy dostawy."""

    title: str | None = Field(default=None, max_length=240)
    firebird_client_id: int | None = Field(default=None, ge=1)
    create_firebird_client: bool = False
    company_name: str = Field(min_length=1, max_length=500)
    company_nip: str | None = Field(default=None, max_length=32)
    company_email: str | None = Field(default=None, max_length=250)
    company_phone: str | None = Field(default=None, max_length=80)
    registered_street: str | None = Field(default=None, max_length=160)
    registered_building_no: str | None = Field(default=None, max_length=20)
    registered_apartment_no: str | None = Field(default=None, max_length=20)
    registered_postal_code: str | None = Field(default=None, max_length=16)
    registered_city: str | None = Field(default=None, max_length=160)
    billing_email: str | None = Field(default=None, max_length=250)
    delivery_date: date | None = None
    delivery_time_window: str | None = Field(default=None, max_length=80)
    delivery_contact_name: str | None = Field(default=None, max_length=160)
    delivery_contact_phone: str | None = Field(default=None, max_length=80)
    delivery_notes: str | None = Field(default=None, max_length=2000)
    service_notes: str | None = Field(default=None, max_length=2000)
    devices: list[DeliveryDevicePayload] = Field(default_factory=list, max_length=100)

    @field_validator(
        "title",
        "company_name",
        "company_nip",
        "company_email",
        "company_phone",
        "registered_street",
        "registered_building_no",
        "registered_apartment_no",
        "registered_postal_code",
        "registered_city",
        "billing_email",
        "delivery_time_window",
        "delivery_contact_name",
        "delivery_contact_phone",
        "delivery_notes",
        "service_notes",
    )
    @classmethod
    def _strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class PickupCreateRequest(BaseModel):
    """Żądanie utworzenia sprawy odbioru urządzeń od klienta."""

    firebird_client_id: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=240)
    delivery_date: date | None = None
    delivery_time_window: str | None = Field(default=None, max_length=80)
    delivery_contact_name: str | None = Field(default=None, max_length=160)
    delivery_contact_phone: str | None = Field(default=None, max_length=80)
    delivery_notes: str | None = Field(default=None, max_length=2000)
    service_notes: str | None = Field(default=None, max_length=2000)
    devices: list[DeliveryDevicePayload] = Field(default_factory=list, max_length=100)

    @field_validator(
        "title",
        "delivery_time_window",
        "delivery_contact_name",
        "delivery_contact_phone",
        "delivery_notes",
        "service_notes",
    )
    @classmethod
    def _strip_pickup_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class DeliveryScheduleUpdateRequest(BaseModel):
    """Aktualizacja ustaleń sprawy dostawy albo odbioru."""

    title: str | None = Field(default=None, max_length=240)
    delivery_date: date | None = None
    delivery_time_window: str | None = Field(default=None, max_length=80)
    delivery_contact_name: str | None = Field(default=None, max_length=160)
    delivery_contact_phone: str | None = Field(default=None, max_length=80)
    delivery_notes: str | None = Field(default=None, max_length=2000)
    service_notes: str | None = Field(default=None, max_length=2000)
    status: Literal["new", "planned", "in_progress", "done", "cancelled"] | None = None

    @field_validator(
        "title",
        "delivery_time_window",
        "delivery_contact_name",
        "delivery_contact_phone",
        "delivery_notes",
        "service_notes",
    )
    @classmethod
    def _strip_update_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class DeliveryDevicesUpdateRequest(BaseModel):
    """Zmiana listy urządzeń w sprawie."""

    mode: Literal["replace", "append"] = "replace"
    device_role: Literal["delivery", "pickup"] = "delivery"
    devices: list[DeliveryDevicePayload] = Field(default_factory=list, max_length=200)
    sync_sheet: bool = True


class DeliveryTaskCreateRequest(BaseModel):
    """Nowe zadanie operacyjne w sprawie."""

    task_type: Literal[
        "delivery",
        "preparation",
        "pickup",
        "zerowka",
        "customer_contact",
        "service_order",
        "document",
        "other",
    ] = "other"
    title: str = Field(min_length=1, max_length=240)
    due_date: date | None = None
    due_time_window: str | None = Field(default=None, max_length=80)
    assignee_user_id: int | None = Field(default=None, ge=1)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("title", "due_time_window", "notes")
    @classmethod
    def _strip_task_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class DeliveryTaskUpdateRequest(BaseModel):
    """Aktualizacja zadania operacyjnego."""

    task_type: (
        Literal[
            "delivery",
            "preparation",
            "pickup",
            "zerowka",
            "customer_contact",
            "service_order",
            "document",
            "other",
        ]
        | None
    ) = None
    status: Literal["todo", "planned", "done", "cancelled"] | None = None
    title: str | None = Field(default=None, max_length=240)
    due_date: date | None = None
    due_time_window: str | None = Field(default=None, max_length=80)
    assignee_user_id: int | None = Field(default=None, ge=1)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("title", "due_time_window", "notes")
    @classmethod
    def _strip_task_update_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class DeliveryDocumentGenerateRequest(BaseModel):
    """Żądanie wygenerowania dokumentu z szablonu DOCX."""

    template_key: str = Field(min_length=1, max_length=260)
    file_name: str | None = Field(default=None, max_length=260)

    @field_validator("template_key", "file_name")
    @classmethod
    def _strip_document_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class GrenkeContractConfirmRequest(BaseModel):
    """Potwierdzenie daty końca umowy GRENKE."""

    confirmed_end_date: date
    contract_number: str | None = Field(default=None, max_length=160)

    @field_validator("contract_number")
    @classmethod
    def _strip_contract(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


async def _ensure_delivery_access(session: AsyncSession, user) -> None:
    if user.role not in {"admin", "operator", "serwisant"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operacja wymaga konta administratora, operatora albo serwisanta.",
        )
    if not await section_permissions.user_has_section(session, user, "delivery"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Konto nie ma uprawnień do modułu obsługi dostaw.",
        )


def _manual_payload_for_firebird(payload: ManualDeliveryCreateRequest) -> dict[str, str]:
    return {
        "company_name": payload.company_name,
        "company_nip": payload.company_nip or "",
        "company_email": payload.company_email or "",
        "company_phone": payload.company_phone or "",
        "registered_street": payload.registered_street or "",
        "registered_building_no": payload.registered_building_no or "",
        "registered_apartment_no": payload.registered_apartment_no or "",
        "registered_postal_code": payload.registered_postal_code or "",
        "registered_city": payload.registered_city or "",
        "billing_email": payload.billing_email or payload.company_email or "",
    }


def _user_label(user) -> str:
    label = " ".join(
        part.strip()
        for part in [
            getattr(user, "first_name", None) or "",
            getattr(user, "last_name", None) or "",
        ]
        if part and part.strip()
    ).strip()
    return label or getattr(user, "email", None) or "CTIP"


def _safe_file_name(value: str | None, *, fallback: str) -> str:
    raw = Path(str(value or fallback)).name.strip()
    cleaned = "".join("_" if char in '<>:"/\\|?*\x00\r\n\t' else char for char in raw).strip(" .")
    return cleaned or fallback


def _device_snapshot(payload: DeliveryDevicePayload) -> dict[str, Any]:
    snapshot = dict(payload.snapshot or {})
    for key, value in payload.model_dump(exclude_none=True).items():
        if key == "snapshot":
            continue
        snapshot.setdefault(key, value)
    return snapshot


def _device_from_payload(
    payload: DeliveryDevicePayload,
    *,
    delivery_case_id: int,
    device_role: str,
) -> DeliveryCaseDevice:
    source_row = (
        payload.source_row or payload.row or payload.ms_id_magazyn_table or payload.id_maszyna_table
    )
    source_type = normalize_workflow_device_source_type(
        payload.source_type, default="firebird_magazyn_28"
    )
    firebird_machine_id = payload.firebird_machine_id or payload.ms_id_maszyna or payload.id_maszyna
    return DeliveryCaseDevice(
        delivery_case_id=delivery_case_id,
        producer=payload.producer,
        model=payload.model,
        serial=payload.serial or payload.serial2,
        ewidencja=payload.ewidencja,
        firebird_machine_id=firebird_machine_id,
        device_role=device_role,
        source_type=source_type,
        source_row=source_row,
        snapshot=_device_snapshot(payload),
    )


def _device_conflict_key(payload: DeliveryDevicePayload) -> str | None:
    source_row = (
        payload.source_row or payload.row or payload.ms_id_magazyn_table or payload.id_maszyna_table
    )
    source_type = normalize_workflow_device_source_type(
        payload.source_type, default="firebird_magazyn_28"
    )
    return build_workflow_device_key(source_type, source_row)


def _client_match_payload(match) -> dict[str, Any]:
    return {
        "found": bool(match.found),
        "id_klient": match.id_klient,
        "nazwa": match.nazwa,
        "nip": match.nip,
        "telefon": match.telefon,
        "email": match.email,
        "error": match.error,
    }


async def _run_firebird_call(session: AsyncSession, func, *args, **kwargs):  # noqa: ANN001
    firebird_config = await load_firebird_runtime_config(session)
    with use_firebird_runtime_config(firebird_config):
        return await asyncio.to_thread(func, *args, **kwargs)


async def _sync_delivery_devices_to_sheet(
    session: AsyncSession,
    *,
    case: DeliveryCase,
    devices: list[DeliveryCaseDevice],
    assignee_label: str,
) -> dict[str, Any]:
    payloads = [
        build_delivery_sheet_payload(device)
        for device in devices
        if device.device_role == DELIVERY_DEVICE_ROLE_DELIVERY
    ]
    if not payloads:
        return {"enabled": False, "reason": "Brak urządzeń dostawy do synchronizacji.", "rows": []}
    sheet_config = await load_workflow_sheet_runtime_config(session)
    with use_workflow_sheet_runtime_config(sheet_config):
        result = await asyncio.to_thread(
            sync_workflow_devices_to_sheet,
            devices=payloads,
            assignee_label=assignee_label,
            proforma_number="",
            form_request_id=case.form_request_id,
            workflow_case_id=case.workflow_case_id,
            business_status_label="Obsługa dostaw CTIP",
            reservation_client_name=case.customer_name,
        )
    operation = "synced" if result.get("enabled") else "pending"
    apply_sheet_sync_snapshot_to_delivery_devices(
        devices,
        operation=operation,
        sheet_result=result,
        assignee_label=assignee_label,
    )
    return result


async def _release_delivery_devices_from_sheet(
    session: AsyncSession,
    *,
    devices: list[DeliveryCaseDevice],
) -> dict[str, Any] | None:
    payloads = [
        build_delivery_sheet_payload(device)
        for device in devices
        if device.device_role == DELIVERY_DEVICE_ROLE_DELIVERY
    ]
    if not payloads:
        return None
    sheet_config = await load_workflow_sheet_runtime_config(session)
    with use_workflow_sheet_runtime_config(sheet_config):
        return await asyncio.to_thread(release_workflow_devices_from_sheet, devices=payloads)


@router.get("/clients/search", summary="Wyszukaj klienta w Menadżerze Serwisu")
async def search_delivery_clients(
    q: str | None = Query(default=None, min_length=2),
    nip: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    """Wyszukuje klienta MS po nazwie albo NIP."""
    _, user = admin_context
    await _ensure_delivery_access(session, user)
    items = await _run_firebird_call(session, search_clients_in_firebird, q, nip=nip, limit=limit)
    return {"items": [_client_match_payload(item) for item in items]}


@router.get("/clients/{client_id}/devices", summary="Urządzenia klienta z Menadżera Serwisu")
async def delivery_client_devices(
    client_id: int,
    include_inactive: bool = False,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    """Pobiera urządzenia przypisane do klienta MS, bez zmiany bazy Firebird."""
    _, user = admin_context
    await _ensure_delivery_access(session, user)
    try:
        items = await _run_firebird_call(
            session,
            load_client_devices_from_firebird,
            client_id,
            include_inactive=include_inactive,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"items": [item.as_dict() for item in items]}


@router.get("/devices/available", summary="Dostępne urządzenia magazynowe")
async def delivery_available_devices(
    limit: int = Query(default=500, ge=1, le=2000),
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    """Zwraca magazyn MS z informacją o lokalnych rezerwacjach CTIP."""
    _, user = admin_context
    await _ensure_delivery_access(session, user)
    try:
        items = await _run_firebird_call(
            session,
            load_available_devices_from_firebird_warehouse,
            limit=limit,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    reservations = await list_active_device_reservations(session)
    output = []
    for item in items:
        source_type = normalize_workflow_device_source_type(
            item.get("source_type"), default="firebird_magazyn_28"
        )
        key = build_workflow_device_key(source_type, item.get("row"))
        reservation = reservations.get(key or "")
        enriched = dict(item)
        enriched["source_type"] = source_type
        enriched["reserved_in_ctip"] = reservation is not None
        enriched["ctip_reservation"] = reservation
        output.append(enriched)
    return {"items": output}


@router.get("/cases", summary="Lista spraw obsługi dostaw")
async def delivery_cases(
    source: Literal["grenke", "manual"] | None = None,
    case_type: Literal["delivery", "pickup"] | None = None,
    status_filter: Literal["new", "planned", "in_progress", "done", "cancelled"] | None = None,
    include_done: bool = True,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    """Zwraca sprawy dostaw widoczne dla modułu /delivery."""
    _, user = admin_context
    await _ensure_delivery_access(session, user)
    cases = await list_delivery_cases(
        session,
        source=source,
        case_type=case_type,
        status=status_filter,
        include_done=include_done,
    )
    return {"items": [serialize_delivery_case(item, include_mailbox_files=False) for item in cases]}


@router.get("/cases/{case_id}", summary="Szczegóły sprawy dostawy")
async def delivery_case_detail(
    case_id: int,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    """Zwraca pełne szczegóły sprawy, w tym pliki i zadania."""
    _, user = admin_context
    await _ensure_delivery_access(session, user)
    case = await load_delivery_case(session, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Sprawa dostawy nie istnieje.")
    return {"item": serialize_delivery_case(case)}


@router.post("/cases", status_code=status.HTTP_201_CREATED, summary="Utwórz ręczną sprawę dostawy")
async def create_manual_delivery_case(
    payload: ManualDeliveryCreateRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    """Tworzy sprawę dostawy niezależną od GRENKE."""
    admin_session, user = admin_context
    await _ensure_delivery_access(session, user)

    firebird_client_id = payload.firebird_client_id
    customer_name = payload.company_name
    customer_nip = normalize_nip(payload.company_nip or "") or None
    customer_email = payload.company_email
    customer_phone = payload.company_phone

    if payload.firebird_client_id:
        match = await _run_firebird_call(
            session, find_client_in_firebird_by_id, payload.firebird_client_id
        )
        if match.error:
            raise HTTPException(status_code=502, detail=match.error)
        if not match.found:
            raise HTTPException(status_code=404, detail="Nie znaleziono klienta Firebird.")
        firebird_client_id = match.id_klient
        customer_name = match.nazwa or customer_name
        customer_nip = normalize_nip(match.nip or customer_nip or "") or customer_nip
        customer_email = match.email or customer_email
        customer_phone = match.telefon or customer_phone
    elif payload.create_firebird_client:
        try:
            result = await _run_firebird_call(
                session,
                create_client_from_submitted_payload,
                _manual_payload_for_firebird(payload),
                source_name="CTIP obsługa dostaw",
            )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        firebird_client_id = result.match.id_klient
        customer_name = result.match.nazwa or customer_name
        customer_nip = normalize_nip(result.match.nip or customer_nip or "") or customer_nip
        customer_email = result.match.email or customer_email
        customer_phone = result.match.telefon or customer_phone

    now = datetime.now(UTC)
    case = DeliveryCase(
        source=DELIVERY_SOURCE_MANUAL,
        case_type=DELIVERY_CASE_TYPE_DELIVERY,
        status="planned" if payload.delivery_date else "new",
        title=payload.title or f"Dostawa: {customer_name}",
        firebird_client_id=firebird_client_id,
        customer_name=customer_name,
        customer_nip=customer_nip,
        customer_email=customer_email,
        customer_phone=customer_phone,
        delivery_date=payload.delivery_date,
        delivery_time_window=payload.delivery_time_window,
        delivery_contact_name=payload.delivery_contact_name,
        delivery_contact_phone=payload.delivery_contact_phone,
        delivery_notes=payload.delivery_notes,
        service_notes=payload.service_notes,
        created_by=user.id,
        updated_by=user.id,
        created_at=now,
        updated_at=now,
        snapshot={"manual": True},
    )
    session.add(case)
    await session.flush()
    for device in payload.devices:
        session.add(
            _device_from_payload(
                device,
                delivery_case_id=case.id,
                device_role=DELIVERY_DEVICE_ROLE_DELIVERY,
            )
        )
    session.add(
        AdminAuditLog(
            user_id=user.id,
            action="delivery_case_create_manual",
            client_ip=admin_session.client_ip,
            payload={"delivery_case_id": case.id, "firebird_client_id": firebird_client_id},
            created_at=now,
        )
    )
    await session.commit()
    refreshed = await load_delivery_case(session, case.id)
    return {"ok": True, "item": serialize_delivery_case(refreshed or case)}


@router.post("/pickups", status_code=status.HTTP_201_CREATED, summary="Zaplanuj odbiór urządzeń")
async def create_pickup_case(
    payload: PickupCreateRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    """Tworzy sprawę odbioru urządzeń od klienta bez zmian w MS na etapie planowania."""
    admin_session, user = admin_context
    await _ensure_delivery_access(session, user)
    match = await _run_firebird_call(
        session, find_client_in_firebird_by_id, payload.firebird_client_id
    )
    if match.error:
        raise HTTPException(status_code=502, detail=match.error)
    if not match.found:
        raise HTTPException(status_code=404, detail="Nie znaleziono klienta Firebird.")

    now = datetime.now(UTC)
    case = DeliveryCase(
        source=DELIVERY_SOURCE_MANUAL,
        case_type=DELIVERY_CASE_TYPE_PICKUP,
        status="planned" if payload.delivery_date else "new",
        title=payload.title or f"Odbiór: {match.nazwa or payload.firebird_client_id}",
        firebird_client_id=match.id_klient,
        customer_name=match.nazwa,
        customer_nip=normalize_nip(match.nip or "") or match.nip,
        customer_email=match.email,
        customer_phone=match.telefon,
        delivery_date=payload.delivery_date,
        delivery_time_window=payload.delivery_time_window,
        delivery_contact_name=payload.delivery_contact_name,
        delivery_contact_phone=payload.delivery_contact_phone,
        delivery_notes=payload.delivery_notes,
        service_notes=payload.service_notes,
        created_by=user.id,
        updated_by=user.id,
        created_at=now,
        updated_at=now,
        snapshot={"manual": True, "pickup": True},
    )
    session.add(case)
    await session.flush()
    for device in payload.devices:
        session.add(
            _device_from_payload(
                device,
                delivery_case_id=case.id,
                device_role=DELIVERY_DEVICE_ROLE_PICKUP,
            )
        )
    if payload.delivery_date:
        session.add(
            DeliveryCaseTask(
                delivery_case_id=case.id,
                task_type="pickup",
                status="planned",
                title="Odbiór urządzeń od klienta",
                due_date=payload.delivery_date,
                due_time_window=payload.delivery_time_window,
                created_at=now,
                updated_at=now,
            )
        )
    session.add(
        AdminAuditLog(
            user_id=user.id,
            action="delivery_case_create_pickup",
            client_ip=admin_session.client_ip,
            payload={"delivery_case_id": case.id, "firebird_client_id": match.id_klient},
            created_at=now,
        )
    )
    await session.commit()
    refreshed = await load_delivery_case(session, case.id)
    return {"ok": True, "item": serialize_delivery_case(refreshed or case)}


@router.patch("/cases/{case_id}", summary="Aktualizuj ustalenia sprawy")
async def update_delivery_case(
    case_id: int,
    payload: DeliveryScheduleUpdateRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    """Aktualizuje termin, notatki i status sprawy dostawy lub odbioru."""
    admin_session, user = admin_context
    await _ensure_delivery_access(session, user)
    case = await load_delivery_case(session, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Sprawa dostawy nie istnieje.")
    fields_set = payload.model_fields_set
    if "title" in fields_set and payload.title:
        case.title = payload.title
    if "delivery_date" in fields_set:
        case.delivery_date = payload.delivery_date
    if "delivery_time_window" in fields_set:
        case.delivery_time_window = payload.delivery_time_window
    if "delivery_contact_name" in fields_set:
        case.delivery_contact_name = payload.delivery_contact_name
    if "delivery_contact_phone" in fields_set:
        case.delivery_contact_phone = payload.delivery_contact_phone
    if "delivery_notes" in fields_set:
        case.delivery_notes = payload.delivery_notes
    if "service_notes" in fields_set:
        case.service_notes = payload.service_notes
    if "status" in fields_set and payload.status:
        case.status = payload.status
    elif "delivery_date" in fields_set and payload.delivery_date and case.status == "new":
        case.status = "planned"
    case.updated_by = user.id
    case.updated_at = datetime.now(UTC)
    session.add(
        AdminAuditLog(
            user_id=user.id,
            action="delivery_case_update",
            client_ip=admin_session.client_ip,
            payload={"delivery_case_id": case.id, "status": case.status},
            created_at=datetime.now(UTC),
        )
    )
    await session.commit()
    refreshed = await load_delivery_case(session, case.id)
    return {"ok": True, "item": serialize_delivery_case(refreshed or case)}


@router.post("/cases/{case_id}/devices", summary="Zapisz urządzenia sprawy")
async def save_delivery_case_devices(
    case_id: int,
    payload: DeliveryDevicesUpdateRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    """Dodaje lub zastępuje urządzenia sprawy oraz rezerwuje je w arkuszu, jeśli dotyczy."""
    admin_session, user = admin_context
    await _ensure_delivery_access(session, user)
    case = await load_delivery_case(session, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Sprawa dostawy nie istnieje.")

    effective_role = payload.device_role
    if case.case_type == DELIVERY_CASE_TYPE_PICKUP:
        effective_role = DELIVERY_DEVICE_ROLE_PICKUP

    if effective_role == DELIVERY_DEVICE_ROLE_DELIVERY:
        reservations = await list_active_device_reservations(
            session, exclude_delivery_case_id=case.id
        )
        conflicts = []
        for device in payload.devices:
            key = _device_conflict_key(device)
            if key and key in reservations:
                conflicts.append({"source_key": key, **reservations[key]})
        if conflicts:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": "Wybrane urządzenie jest już zarezerwowane w CTIP.",
                    "conflicts": conflicts,
                },
            )

    now = datetime.now(UTC)
    old_devices = list(case.devices or [])
    sheet_warning: str | None = None
    sheet_result: dict[str, Any] | None = None
    release_result: dict[str, Any] | None = None
    if payload.mode == "replace":
        removed_sheet_devices = [
            device for device in old_devices if device.device_role == DELIVERY_DEVICE_ROLE_DELIVERY
        ]
        if removed_sheet_devices and payload.sync_sheet:
            try:
                release_result = await _release_delivery_devices_from_sheet(
                    session,
                    devices=removed_sheet_devices,
                )
            except Exception as exc:  # noqa: BLE001
                sheet_warning = (
                    str(exc).strip() or "Nie udało się zwolnić poprzednich rezerwacji w arkuszu."
                )
        await session.execute(
            delete(DeliveryCaseDevice).where(DeliveryCaseDevice.delivery_case_id == case.id)
        )
        case.devices = []
        await session.flush()

    new_devices = [
        _device_from_payload(device, delivery_case_id=case.id, device_role=effective_role)
        for device in payload.devices
    ]
    session.add_all(new_devices)
    await session.flush()
    if new_devices and effective_role == DELIVERY_DEVICE_ROLE_DELIVERY and payload.sync_sheet:
        try:
            sheet_result = await _sync_delivery_devices_to_sheet(
                session,
                case=case,
                devices=new_devices,
                assignee_label=_user_label(user),
            )
        except Exception as exc:  # noqa: BLE001
            sheet_warning = str(exc).strip() or "Nie udało się zaktualizować arkusza urządzeń."
            apply_sheet_sync_snapshot_to_delivery_devices(
                new_devices,
                operation="error",
                sheet_result=None,
                assignee_label=_user_label(user),
                error=sheet_warning,
            )
    case.updated_at = now
    case.updated_by = user.id
    session.add(
        AdminAuditLog(
            user_id=user.id,
            action="delivery_case_devices_save",
            client_ip=admin_session.client_ip,
            payload={
                "delivery_case_id": case.id,
                "mode": payload.mode,
                "device_role": effective_role,
                "devices_count": len(payload.devices),
                "sheet_warning": sheet_warning,
            },
            created_at=now,
        )
    )
    await session.commit()
    refreshed = await load_delivery_case(session, case.id)
    return {
        "ok": True,
        "item": serialize_delivery_case(refreshed or case),
        "sheet_result": sheet_result,
        "release_result": release_result,
        "sheet_warning": sheet_warning,
    }


@router.get("/cases/{case_id}/tasks", summary="Lista zadań sprawy")
async def delivery_case_tasks(
    case_id: int,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    """Zwraca zadania operacyjne sprawy."""
    _, user = admin_context
    await _ensure_delivery_access(session, user)
    case = await load_delivery_case(session, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Sprawa dostawy nie istnieje.")
    return {"items": [serialize_delivery_task(task) for task in case.tasks]}


@router.post(
    "/cases/{case_id}/tasks", status_code=status.HTTP_201_CREATED, summary="Dodaj zadanie sprawy"
)
async def create_delivery_case_task(
    case_id: int,
    payload: DeliveryTaskCreateRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    """Tworzy zadanie przygotowania, dowozu, odbioru, zerówki albo kontaktu."""
    admin_session, user = admin_context
    await _ensure_delivery_access(session, user)
    case = await load_delivery_case(session, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Sprawa dostawy nie istnieje.")
    now = datetime.now(UTC)
    task = DeliveryCaseTask(
        delivery_case_id=case.id,
        task_type=payload.task_type,
        status="planned" if payload.due_date else "todo",
        title=payload.title,
        due_date=payload.due_date,
        due_time_window=payload.due_time_window,
        assignee_user_id=payload.assignee_user_id,
        notes=payload.notes,
        created_at=now,
        updated_at=now,
    )
    session.add(task)
    case.updated_at = now
    case.updated_by = user.id
    session.add(
        AdminAuditLog(
            user_id=user.id,
            action="delivery_case_task_create",
            client_ip=admin_session.client_ip,
            payload={"delivery_case_id": case.id, "task_type": task.task_type},
            created_at=now,
        )
    )
    await session.commit()
    return {"ok": True, "item": serialize_delivery_task(task)}


@router.patch("/cases/{case_id}/tasks/{task_id}", summary="Aktualizuj zadanie sprawy")
async def update_delivery_case_task(
    case_id: int,
    task_id: int,
    payload: DeliveryTaskUpdateRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    """Aktualizuje status i szczegóły zadania operacyjnego."""
    admin_session, user = admin_context
    await _ensure_delivery_access(session, user)
    case = await load_delivery_case(session, case_id)
    task = await load_delivery_task(session, task_id)
    if case is None or task is None or task.delivery_case_id != case.id:
        raise HTTPException(status_code=404, detail="Zadanie sprawy nie istnieje.")
    fields_set = payload.model_fields_set
    if "task_type" in fields_set and payload.task_type:
        task.task_type = payload.task_type
    if "status" in fields_set and payload.status:
        task.status = payload.status
    if "title" in fields_set and payload.title:
        task.title = payload.title
    if "due_date" in fields_set:
        task.due_date = payload.due_date
    if "due_time_window" in fields_set:
        task.due_time_window = payload.due_time_window
    if "assignee_user_id" in fields_set:
        task.assignee_user_id = payload.assignee_user_id
    if "notes" in fields_set:
        task.notes = payload.notes
    now = datetime.now(UTC)
    task.updated_at = now
    case.updated_at = now
    case.updated_by = user.id
    session.add(
        AdminAuditLog(
            user_id=user.id,
            action="delivery_case_task_update",
            client_ip=admin_session.client_ip,
            payload={"delivery_case_id": case.id, "task_id": task.id, "status": task.status},
            created_at=now,
        )
    )
    await session.commit()
    return {"ok": True, "item": serialize_delivery_task(task)}


@router.get("/cases/{case_id}/files", summary="Lista plików sprawy")
async def delivery_case_files(
    case_id: int,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    """Zwraca pliki dodane ręcznie, wygenerowane i archiwalne pliki mailboxa."""
    _, user = admin_context
    await _ensure_delivery_access(session, user)
    case = await load_delivery_case(session, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Sprawa dostawy nie istnieje.")
    data = serialize_delivery_case(case)
    return {"items": [*data["files"], *data["mailbox_files"]]}


@router.post(
    "/cases/{case_id}/files", status_code=status.HTTP_201_CREATED, summary="Dodaj plik do sprawy"
)
async def upload_delivery_case_file(
    case_id: int,
    file_type: str = Query(default="other", max_length=80),
    upload: UploadFile = DELIVERY_UPLOAD_FILE,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    """Zapisuje załącznik sprawy w katalogu modułu dostaw."""
    admin_session, user = admin_context
    await _ensure_delivery_access(session, user)
    case = await load_delivery_case(session, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Sprawa dostawy nie istnieje.")
    raw = await upload.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Nie przesłano pliku.")
    now = datetime.now(UTC)
    safe_name = _safe_file_name(upload.filename, fallback=f"plik_{now.strftime('%Y%m%d_%H%M%S')}")
    folder = delivery_files_root() / f"case_{case.id}"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{now.strftime('%Y%m%d_%H%M%S')}_{safe_name}"
    target.write_bytes(raw)
    file_item = DeliveryCaseFile(
        delivery_case_id=case.id,
        file_type=file_type.strip() or "other",
        source="upload",
        file_name=target.name,
        original_name=upload.filename,
        path=target.as_posix(),
        content_type=upload.content_type,
        size_bytes=len(raw),
        uploaded_by=user.id,
        created_at=now,
    )
    session.add(file_item)
    case.updated_at = now
    case.updated_by = user.id
    session.add(
        AdminAuditLog(
            user_id=user.id,
            action="delivery_case_file_upload",
            client_ip=admin_session.client_ip,
            payload={"delivery_case_id": case.id, "file_name": file_item.file_name},
            created_at=now,
        )
    )
    await session.commit()
    return {"ok": True, "item": serialize_delivery_file(file_item)}


@router.get("/files/{file_id}/download", summary="Pobierz plik sprawy")
async def download_delivery_file(
    file_id: int,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
):
    """Pobiera plik zapisany w module dostaw."""
    _, user = admin_context
    await _ensure_delivery_access(session, user)
    file_item = await load_delivery_file(session, file_id)
    if file_item is None:
        raise HTTPException(status_code=404, detail="Plik nie istnieje.")
    path = Path(file_item.path)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Plik nie istnieje na dysku.")
    media_type = (
        file_item.content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    )
    return FileResponse(
        path, media_type=media_type, filename=file_item.original_name or file_item.file_name
    )


@router.get(
    "/cases/{case_id}/mailbox-files/{file_index}/download", summary="Pobierz plik umowy z mailboxa"
)
async def download_delivery_mailbox_file(
    case_id: int,
    file_index: int,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
):
    """Pobiera archiwalny plik umowy GRENKE zapisany przez synchronizację skrzynki."""
    _, user = admin_context
    await _ensure_delivery_access(session, user)
    case = await load_delivery_case(session, case_id)
    if case is None or case.workflow_case is None:
        raise HTTPException(status_code=404, detail="Sprawa albo plik nie istnieje.")
    snapshot = (
        case.workflow_case.client_payload_snapshot
        if isinstance(case.workflow_case.client_payload_snapshot, dict)
        else {}
    )
    files = archived_contract_files_from_snapshot(snapshot)
    if file_index < 0 or file_index >= len(files):
        raise HTTPException(status_code=404, detail="Plik mailboxa nie istnieje.")
    meta = files[file_index]
    path = Path(str(meta.get("path") or ""))
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Plik nie istnieje na dysku.")
    media_type = mimetypes.guess_type(path.name)[0] or "application/pdf"
    return FileResponse(
        path, media_type=media_type, filename=str(meta.get("file_name") or path.name)
    )


@router.get("/document-templates", summary="Lista szablonów dokumentów")
async def delivery_document_templates(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    """Zwraca zarejestrowane i wykryte wzory dokumentów dostaw."""
    _, user = admin_context
    await _ensure_delivery_access(session, user)
    return {"items": await list_document_templates(session)}


@router.post(
    "/cases/{case_id}/documents/generate",
    status_code=status.HTTP_201_CREATED,
    summary="Generuj dokument DOCX",
)
async def generate_delivery_document(
    case_id: int,
    payload: DeliveryDocumentGenerateRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    """Wypełnia szablon DOCX placeholderami sprawy i zapisuje wynik jako plik sprawy."""
    admin_session, user = admin_context
    await _ensure_delivery_access(session, user)
    case = await load_delivery_case(session, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Sprawa dostawy nie istnieje.")
    try:
        template_path = resolve_document_template_path(payload.template_key)
        content = render_docx_template(template_path, build_document_context(case))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    now = datetime.now(UTC)
    default_name = f"sprawa_{case.id}_{template_path.stem}.docx"
    output_name = _safe_file_name(payload.file_name, fallback=default_name)
    if not output_name.lower().endswith(".docx"):
        output_name = f"{output_name}.docx"
    folder = delivery_files_root() / f"case_{case.id}"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{now.strftime('%Y%m%d_%H%M%S')}_{output_name}"
    target.write_bytes(content)
    file_item = DeliveryCaseFile(
        delivery_case_id=case.id,
        file_type="generated_document",
        source="generated",
        file_name=target.name,
        original_name=output_name,
        path=target.as_posix(),
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        size_bytes=len(content),
        uploaded_by=user.id,
        created_at=now,
        snapshot={"template_key": payload.template_key, "template_path": template_path.as_posix()},
    )
    session.add(file_item)
    case.updated_at = now
    case.updated_by = user.id
    session.add(
        AdminAuditLog(
            user_id=user.id,
            action="delivery_case_document_generate",
            client_ip=admin_session.client_ip,
            payload={"delivery_case_id": case.id, "template_key": payload.template_key},
            created_at=now,
        )
    )
    await session.commit()
    return {"ok": True, "item": serialize_delivery_file(file_item)}


@router.get("/grenke-contracts", summary="Kalendarz końców umów GRENKE")
async def grenke_contracts(
    status_filter: Literal["pending_confirmation", "confirmed", "cancelled"] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    q: str | None = Query(default=None, max_length=160),
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    """Zwraca wpisy końców umów, w tym oczekujące na potwierdzenie."""
    _, user = admin_context
    await _ensure_delivery_access(session, user)
    items = await list_grenke_contract_ends(
        session,
        status=status_filter,
        date_from=date_from,
        date_to=date_to,
        q=q,
    )
    return {"items": [serialize_grenke_contract_end(item) for item in items]}


@router.post("/grenke-contracts/{item_id}/confirm", summary="Potwierdź datę końca umowy GRENKE")
async def confirm_contract_end(
    item_id: int,
    payload: GrenkeContractConfirmRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    """Potwierdza datę końca umowy i aktywuje przypomnienia."""
    admin_session, user = admin_context
    await _ensure_delivery_access(session, user)
    item = await load_grenke_contract_end(session, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Wpis końca umowy nie istnieje.")
    await confirm_grenke_contract_end(
        session,
        item=item,
        confirmed_end_date=payload.confirmed_end_date,
        confirmed_by=user.id,
        contract_number=payload.contract_number,
    )
    session.add(
        AdminAuditLog(
            user_id=user.id,
            action="grenke_contract_end_confirm",
            client_ip=admin_session.client_ip,
            payload={
                "grenke_contract_end_id": item.id,
                "date": payload.confirmed_end_date.isoformat(),
            },
            created_at=datetime.now(UTC),
        )
    )
    await session.commit()
    return {"ok": True, "item": serialize_grenke_contract_end(item)}


@router.post("/grenke-contracts/{item_id}/cancel", summary="Anuluj wpis końca umowy GRENKE")
async def cancel_contract_end(
    item_id: int,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    """Wyłącza wpis z kalendarza końców umów."""
    admin_session, user = admin_context
    await _ensure_delivery_access(session, user)
    item = await load_grenke_contract_end(session, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Wpis końca umowy nie istnieje.")
    item.status = CONTRACT_END_CANCELLED
    item.updated_at = datetime.now(UTC)
    session.add(
        AdminAuditLog(
            user_id=user.id,
            action="grenke_contract_end_cancel",
            client_ip=admin_session.client_ip,
            payload={"grenke_contract_end_id": item.id},
            created_at=datetime.now(UTC),
        )
    )
    await session.commit()
    return {"ok": True, "item": serialize_grenke_contract_end(item)}


@router.post("/grenke-contracts/reminders/run", summary="Uruchom przypomnienia końców umów GRENKE")
async def run_contract_end_reminders(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    """Ręcznie uruchamia przebieg przypomnień dla potwierdzonych końców umów."""
    _, user = admin_context
    await _ensure_delivery_access(session, user)
    result = await send_grenke_contract_end_reminders(session)
    await session.commit()
    return {"ok": True, "result": result}


__all__ = ["router"]
