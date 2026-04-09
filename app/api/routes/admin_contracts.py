"""API dashboardu obslugi umow."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_session_context, get_db_session
from app.models import FormRequest, FormWorkflowCase
from app.services import section_permissions
from app.services.audit import record_audit
from app.services.contracts_dashboard import (
    create_client_from_submitted_payload,
    find_client_in_firebird,
    find_client_in_firebird_by_id,
    find_device_in_firebird,
    firebird_writes_enabled,
    load_contract_forms,
    load_device_from_sheet_row,
    load_devices_from_sheet,
    normalize_nip,
    synchronize_device_from_sheet_row,
)
from app.services.contracts_proforma import create_proforma_from_workflow
from app.services.contracts_workflow import (
    WORKFLOW_BUSINESS_STATUS_DRAFT,
    WORKFLOW_CLIENT_MODE_BASIC_PROFORMA,
    build_client_preview,
    build_sales_packet,
    build_workflow_business_status_options,
    clear_form_workflow_delivery,
    get_form_workflow_case,
    get_or_create_form_workflow_case,
    list_form_workflow_devices,
    map_form_workflow_summaries,
    replace_form_workflow_devices,
    serialize_workflow_case,
    set_form_workflow_business_status,
    set_form_workflow_client,
    set_form_workflow_delivery,
    set_form_workflow_proforma,
)

router = APIRouter(prefix="/admin/contracts", tags=["admin-contracts"])


def _to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC).isoformat()
    return value.astimezone(UTC).isoformat()


class ContractActionRequest(BaseModel):
    """Żądanie akcji workflow dla formularza/urządzenia."""

    entity: str = Field(pattern="^(form|device)$")
    action: str = Field(min_length=3, max_length=64)
    target_id: int | None = Field(default=None, ge=1)
    row: int | None = Field(default=None, ge=1)


class WorkflowClientRequest(BaseModel):
    """Żądanie zapisania klienta do sprawy workflow."""

    mode: str = Field(
        default=WORKFLOW_CLIENT_MODE_BASIC_PROFORMA,
        pattern="^basic_proforma$",
    )


class WorkflowDeviceSelection(BaseModel):
    """Pojedyncze urzadzenie wybrane do sprawy workflow."""

    row: int = Field(ge=1)
    price_net: str | None = Field(default=None, max_length=32)
    price_gross: str | None = Field(default=None, max_length=32)


class WorkflowDevicesRequest(BaseModel):
    """Żądanie zapisania wyboru urządzeń do sprawy workflow."""

    rows: list[int] = Field(default_factory=list, max_length=50)
    devices: list[WorkflowDeviceSelection] = Field(default_factory=list, max_length=50)


class WorkflowStatusRequest(BaseModel):
    """Zadanie zmiany statusu biznesowego sprawy workflow."""

    business_status: str = Field(
        pattern="^(DRAFT|PENDING_APPROVAL|APPROVED|ZEROWKA|REJECTED)$",
    )


class WorkflowProformaRequest(BaseModel):
    """Zadanie utworzenia proformy dla klienta lub banku."""

    for_bank: bool = Field(default=True)


class WorkflowDeliveryRequest(BaseModel):
    """Zadanie zapisania terminu i danych dowozu dla sprawy workflow."""

    delivery_date: date
    delivery_time_window: str | None = Field(default=None, max_length=64)
    delivery_contact_name: str | None = Field(default=None, max_length=160)
    delivery_contact_phone: str | None = Field(default=None, max_length=64)
    delivery_notes: str | None = Field(default=None, max_length=2000)


class WorkflowDeliveryMoveRequest(BaseModel):
    """Zadanie przeniesienia dostawy na inny dzien."""

    delivery_date: date


WORKFLOW_DEFAULT_VAT = Decimal("23")
PRICE_PRECISION = Decimal("0.01")
WORKFLOW_BANK_CLIENT_ID = 855
WORKFLOW_BANK_CLIENT_NIP = normalize_nip("782-22-75-815")
WORKFLOW_BANK_CLIENT_NAME = "GRENKELEASING Sp. z o.o."


def _normalize_price_text(value: str | None) -> str:
    if value is None:
        return ""
    return str(value).strip().replace(",", ".")


def _parse_price(value: str | None) -> Decimal | None:
    text = _normalize_price_text(value)
    if not text:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _format_price(value: Decimal | None) -> str:
    if value is None:
        return ""
    return f"{value.quantize(PRICE_PRECISION, rounding=ROUND_HALF_UP):.2f}"


def _gross_to_net(value: Decimal, vat_rate: Decimal = WORKFLOW_DEFAULT_VAT) -> Decimal:
    multiplier = Decimal("1.00") + (vat_rate / Decimal("100"))
    if multiplier == 0:
        return value.quantize(PRICE_PRECISION, rounding=ROUND_HALF_UP)
    return (value / multiplier).quantize(PRICE_PRECISION, rounding=ROUND_HALF_UP)


def _net_to_gross(value: Decimal, vat_rate: Decimal = WORKFLOW_DEFAULT_VAT) -> Decimal:
    multiplier = Decimal("1.00") + (vat_rate / Decimal("100"))
    if multiplier == 0:
        return value.quantize(PRICE_PRECISION, rounding=ROUND_HALF_UP)
    return (value * multiplier).quantize(PRICE_PRECISION, rounding=ROUND_HALF_UP)


def _build_selected_device_payloads(payload: WorkflowDevicesRequest) -> list[dict[str, str | int]]:
    if payload.devices:
        selected_rows: dict[int, dict[str, str | int]] = {}
        for item in payload.devices:
            selected_rows[int(item.row)] = {
                "row": int(item.row),
                "price_net": _normalize_price_text(item.price_net),
                "price_gross": _normalize_price_text(item.price_gross),
            }
        return [selected_rows[row] for row in sorted(selected_rows)]

    selected_rows = sorted({int(row) for row in payload.rows if int(row) > 0})
    return [
        {
            "row": row,
            "price_net": "",
            "price_gross": "",
        }
        for row in selected_rows
    ]


def _delivery_label(delivery_date: date | None, delivery_time_window: str | None) -> str | None:
    if delivery_date is None:
        return None
    base = delivery_date.isoformat()
    window = str(delivery_time_window or "").strip()
    if not window:
        return base
    return f"{base} ({window})"


def _normalize_schedule_range(
    day_from: date | None,
    day_to: date | None,
) -> tuple[date, date]:
    default_from = datetime.now(UTC).date()
    resolved_from = day_from or default_from
    resolved_to = day_to or (resolved_from + timedelta(days=6))
    if resolved_to < resolved_from:
        raise ValueError("Zakres dat harmonogramu jest nieprawidlowy.")
    return resolved_from, resolved_to


def _schedule_customer_name(workflow_case: FormWorkflowCase, form: FormRequest) -> str:
    payload = workflow_case.client_payload_snapshot
    if isinstance(payload, dict):
        company_name = str(payload.get("company_name") or "").strip()
        if company_name:
            return company_name
    return str(form.customer_name or "")


async def _resolve_proforma_recipient_client_id(
    *,
    for_bank: bool,
    workflow_client_id: int,
) -> tuple[int, str]:
    if not for_bank:
        return workflow_client_id, "klient z formularza"

    by_id = await asyncio.to_thread(find_client_in_firebird_by_id, WORKFLOW_BANK_CLIENT_ID)
    if by_id.error:
        raise RuntimeError(
            f"Nie udalo sie zweryfikowac klienta bankowego ID {WORKFLOW_BANK_CLIENT_ID}: {by_id.error}"
        )
    if by_id.found and by_id.id_klient:
        return by_id.id_klient, f"bank {WORKFLOW_BANK_CLIENT_NAME}"

    by_nip = await asyncio.to_thread(find_client_in_firebird, WORKFLOW_BANK_CLIENT_NIP)
    if by_nip.error:
        raise RuntimeError(
            f"Nie udalo sie wyszukac klienta bankowego po NIP {WORKFLOW_BANK_CLIENT_NIP}: {by_nip.error}"
        )
    if by_nip.found and by_nip.id_klient:
        return by_nip.id_klient, f"bank {WORKFLOW_BANK_CLIENT_NAME}"

    raise ValueError(
        "Nie znaleziono klienta bankowego GRENKELEASING Sp. z o.o. "
        f"(oczekiwany ID {WORKFLOW_BANK_CLIENT_ID}, NIP {WORKFLOW_BANK_CLIENT_NIP})."
    )


@router.get("/dashboard", summary="Dane dashboardu obslugi umow")
async def contracts_dashboard_data(
    forms_scope: str = Query(default="submitted", pattern="^(submitted|all)$"),
    include_devices: bool = Query(default=True),
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Zwraca formularze workflow, dopasowanie klienta i urzadzen."""
    _, admin_user = admin_context
    if admin_user.role not in {"admin", "operator"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operacja wymaga roli administratora lub operatora.",
        )
    if not await section_permissions.user_has_section(session, admin_user, "generator"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Konto nie ma uprawnien do modulu obslugi umow.",
        )

    warnings: list[str] = []

    submitted_only = forms_scope != "all"
    forms = await load_contract_forms(session, limit=300, submitted_only=submitted_only)
    workflow_summaries = await map_form_workflow_summaries(
        session, form_request_ids=[item.id for item in forms]
    )
    await session.commit()

    from app.services import form_generator

    form_status_totals = {
        "GENERATED": 0,
        "DISPATCHED": 0,
        "SUBMITTED": 0,
        "EXPIRED": 0,
    }
    form_items: list[dict] = []
    firebird_client_cache: dict[str, object] = {}

    async def resolve_client_match(nip: str):
        if not nip:
            return None
        if nip not in firebird_client_cache:
            firebird_client_cache[nip] = await asyncio.to_thread(find_client_in_firebird, nip)
        return firebird_client_cache[nip]

    for item in forms:
        form_status_totals[item.status] = form_status_totals.get(item.status, 0) + 1
        payload: dict = {}
        meta: dict = {}
        firebird_match = None
        contract_action: str | None = None

        if item.status == "SUBMITTED":
            decoded_payload, decoded_meta = form_generator.decode_submitted_payload(item)
            payload = decoded_payload or {}
            meta = decoded_meta or {}
            nip = normalize_nip(str(payload.get("company_nip") or ""))
            if nip:
                firebird_match = await resolve_client_match(nip)
                contract_action = "podlacz_klienta" if firebird_match.found else "utworz_klienta"
        else:
            nip = ""

        form_items.append(
            {
                "id": item.id,
                "status": item.status,
                "status_message": form_generator.build_status_message(item),
                "created_at": _to_iso(item.created_at),
                "submitted_at": _to_iso(item.submitted_at),
                "token_expires_at": _to_iso(item.token_expires_at),
                "customer_name": str(payload.get("company_name") or item.customer_name or ""),
                "customer_nip": nip,
                "customer_email": str(payload.get("company_email") or item.customer_email or ""),
                "customer_phone": str(payload.get("company_phone") or item.customer_phone or ""),
                "sms_status": item.sms_status,
                "email_status": item.email_status,
                "payload": payload,
                "meta": meta,
                "firebird": {
                    "found": firebird_match.found if firebird_match else False,
                    "id_klient": firebird_match.id_klient if firebird_match else None,
                    "nazwa": firebird_match.nazwa if firebird_match else None,
                    "nip": firebird_match.nip if firebird_match else None,
                    "telefon": firebird_match.telefon if firebird_match else None,
                    "email": firebird_match.email if firebird_match else None,
                    "error": firebird_match.error if firebird_match else None,
                },
                "contract_action": contract_action,
                "workflow": workflow_summaries.get(item.id, serialize_workflow_case(None)),
            }
        )

    devices_output: list[dict] = []
    if include_devices:
        try:
            sheet_devices = await asyncio.to_thread(load_devices_from_sheet)
        except Exception as exc:  # noqa: BLE001
            sheet_devices = []
            warnings.append(f"Blad odczytu arkusza Urzadzenia: {exc}")
        for device in sheet_devices:
            match = await asyncio.to_thread(
                find_device_in_firebird, device.get("serial"), device.get("ewidencja")
            )
            devices_output.append(
                {
                    "row": int(device.get("row") or 0),
                    "serial": device.get("serial") or "",
                    "ewidencja": device.get("ewidencja") or "",
                    "model": device.get("model") or "",
                    "found_in_firebird": match.found_in_firebird,
                    "id_maszyna": match.id_maszyna,
                    "id_klient": match.id_klient,
                    "id_umowacpc": match.id_umowacpc,
                    "firebird_error": match.error,
                    "sync_action": (
                        "synchronizuj"
                        if (device.get("serial") or device.get("ewidencja"))
                        else "do_weryfikacji"
                    ),
                }
            )

    matched_count = sum(1 for item in devices_output if item["found_in_firebird"])
    return {
        "forms_scope": forms_scope,
        "forms_total": len(form_items),
        "forms_status_totals": form_status_totals,
        "devices_total": len(devices_output),
        "devices_matched": matched_count,
        "forms": form_items,
        "devices": devices_output,
        "warnings": warnings,
    }


@router.get("/forms/{form_id}/workflow", summary="Szczegoly sprawy workflow dla formularza")
async def contracts_form_workflow_detail(
    form_id: int,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Zwraca stan sprawy workflow dla wskazanego formularza SUBMITTED."""
    _, admin_user = admin_context
    if admin_user.role not in {"admin", "operator"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operacja wymaga roli administratora lub operatora.",
        )
    if not await section_permissions.user_has_section(session, admin_user, "generator"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Konto nie ma uprawnien do modulu obslugi umow.",
        )

    from app.services import form_generator

    item = await form_generator.get_form_request_by_id(session, form_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Formularz nie istnieje.",
        )
    if item.status != "SUBMITTED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Workflow jest dostepny tylko dla formularzy ze statusem SUBMITTED.",
        )

    form_payload, form_meta = form_generator.decode_submitted_payload(item)
    submitted_payload = form_payload or {}
    submitted_meta = form_meta or {}
    nip = normalize_nip(str(submitted_payload.get("company_nip") or ""))
    firebird_match = await asyncio.to_thread(find_client_in_firebird, nip) if nip else None

    workflow_case = await get_form_workflow_case(session, form_request_id=item.id)
    workflow_devices = (
        await list_form_workflow_devices(session, workflow_case_id=workflow_case.id)
        if workflow_case is not None
        else []
    )
    saved_devices_by_row = {
        int(device.source_row): device
        for device in workflow_devices
        if device.source_row is not None
    }
    selected_rows = {
        int(device.source_row) for device in workflow_devices if device.source_row is not None
    }

    available_devices = []
    for device in await asyncio.to_thread(load_devices_from_sheet):
        try:
            row_number = int(device.get("row") or 0)
        except ValueError:
            row_number = 0
        saved_device = saved_devices_by_row.get(row_number)
        sheet_gross_price = _format_price(_parse_price(str(device.get("price") or "")))
        saved_net_price = _normalize_price_text(saved_device.price_net) if saved_device else ""
        saved_gross_price = _normalize_price_text(saved_device.price_gross) if saved_device else ""
        computed_net_price = ""
        computed_gross_price = saved_gross_price or sheet_gross_price
        parsed_gross_price = _parse_price(computed_gross_price)
        if saved_net_price:
            computed_net_price = saved_net_price
        elif parsed_gross_price is not None:
            computed_net_price = _format_price(_gross_to_net(parsed_gross_price))
        available_devices.append(
            {
                "row": row_number,
                "producer": device.get("producer") or "",
                "model": device.get("model") or "",
                "serial": device.get("serial") or "",
                "ewidencja": device.get("ewidencja") or "",
                "status": device.get("status") or "",
                "price": device.get("price") or "",
                "price_net": computed_net_price,
                "price_gross": computed_gross_price,
                "vat_rate": "23",
                "reservation": device.get("reservation") or "",
                "reservation_status": device.get("reservation_status") or "",
                "description": device.get("description") or "",
                "ms_id_maszyna": device.get("ms_id_maszyna") or "",
                "ms_id_klient": device.get("ms_id_klient") or "",
                "ms_nazwa_klienta": device.get("ms_nazwa_klienta") or "",
                "ms_nip": device.get("ms_nip") or "",
                "selected": row_number in selected_rows,
            }
        )

    return {
        "form": {
            "id": item.id,
            "status": item.status,
            "customer_name": str(submitted_payload.get("company_name") or item.customer_name or ""),
            "customer_nip": nip,
            "customer_email": str(
                submitted_payload.get("company_email") or item.customer_email or ""
            ),
            "customer_phone": str(
                submitted_payload.get("company_phone") or item.customer_phone or ""
            ),
            "submitted_at": _to_iso(item.submitted_at),
            "payload": submitted_payload,
            "meta": submitted_meta,
        },
        "client_preview": build_client_preview(submitted_payload),
        "workflow": serialize_workflow_case(workflow_case, workflow_devices),
        "workflow_status_action": {
            "current": (
                workflow_case.business_status
                if workflow_case is not None
                else WORKFLOW_BUSINESS_STATUS_DRAFT
            ),
            "options": build_workflow_business_status_options(),
        },
        "firebird": {
            "found": firebird_match.found if firebird_match else False,
            "id_klient": firebird_match.id_klient if firebird_match else None,
            "nazwa": firebird_match.nazwa if firebird_match else None,
            "nip": firebird_match.nip if firebird_match else None,
            "telefon": firebird_match.telefon if firebird_match else None,
            "email": firebird_match.email if firebird_match else None,
            "error": firebird_match.error if firebird_match else None,
        },
        "client_action": {
            "mode": WORKFLOW_CLIENT_MODE_BASIC_PROFORMA,
            "label": "Podstawowe tworzenie na potrzeby proformy",
            "operation": "podlacz" if firebird_match and firebird_match.found else "utworz",
            "button_label": (
                "Potwierdz klienta w Menadzerze Serwisu"
                if firebird_match and firebird_match.found
                else "Dodaj klienta do Menadzera Serwisu"
            ),
        },
        "available_devices": available_devices,
        "selection_capabilities": {
            "search": True,
            "status_filter": True,
            "reservation_filter": True,
            "format_filter": False,
            "color_filter": False,
            "note": (
                "Aktualne zrodlo urzadzen z arkusza nie zawiera wiarygodnych pol A4/A3 i mono/kolor. "
                "Te filtry warto dolaczyc po przejsciu na Menadzer Serwisu lub po uzupelnieniu arkusza. "
                "Ceny w FLOW mozna wpisywac recznie dla kazdego urzadzenia; przeliczenie netto/brutto "
                "na tym etapie korzysta z domyslnej stawki VAT 23%."
            ),
        },
        "sales_packet": build_sales_packet(workflow_case, workflow_devices),
    }


@router.post(
    "/forms/{form_id}/workflow/delivery", summary="Zapisz termin dowozu dla sprawy workflow"
)
async def contracts_form_workflow_delivery_save(
    form_id: int,
    payload: WorkflowDeliveryRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Zapisuje dane harmonogramu dowozu po stronie CTIP."""
    admin_session, admin_user = admin_context
    if admin_user.role not in {"admin", "operator"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operacja wymaga roli administratora lub operatora.",
        )
    if not await section_permissions.user_has_section(session, admin_user, "generator"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Konto nie ma uprawnien do modulu obslugi umow.",
        )

    from app.services import form_generator

    item = await form_generator.get_form_request_by_id(session, form_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Formularz nie istnieje.",
        )
    if item.status != "SUBMITTED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Workflow jest dostepny tylko dla formularzy ze statusem SUBMITTED.",
        )

    submitted_payload, _ = form_generator.decode_submitted_payload(item)
    workflow_case = await get_or_create_form_workflow_case(
        session,
        form=item,
        user_id=admin_user.id,
        payload_snapshot=submitted_payload or {},
    )
    workflow_case = await set_form_workflow_delivery(
        session,
        workflow_case=workflow_case,
        delivery_date=payload.delivery_date,
        delivery_time_window=payload.delivery_time_window,
        delivery_contact_name=payload.delivery_contact_name,
        delivery_contact_phone=payload.delivery_contact_phone,
        delivery_notes=payload.delivery_notes,
        updated_by=admin_user.id,
    )
    workflow_devices = await list_form_workflow_devices(session, workflow_case_id=workflow_case.id)
    await record_audit(
        session,
        user_id=admin_user.id,
        action="contracts_flow_delivery_save",
        client_ip=admin_session.client_ip,
        payload={
            "form_request_id": item.id,
            "workflow_case_id": workflow_case.id,
            "delivery_date": payload.delivery_date.isoformat(),
            "delivery_time_window": payload.delivery_time_window,
        },
    )
    await session.commit()
    return {
        "ok": True,
        "message": "Zapisano termin i dane dowozu.",
        "workflow": serialize_workflow_case(workflow_case, workflow_devices),
    }


@router.delete(
    "/forms/{form_id}/workflow/delivery", summary="Usun termin dowozu dla sprawy workflow"
)
async def contracts_form_workflow_delivery_delete(
    form_id: int,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Usuwa dane harmonogramu dowozu po stronie CTIP."""
    admin_session, admin_user = admin_context
    if admin_user.role not in {"admin", "operator"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operacja wymaga roli administratora lub operatora.",
        )
    if not await section_permissions.user_has_section(session, admin_user, "generator"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Konto nie ma uprawnien do modulu obslugi umow.",
        )

    from app.services import form_generator

    item = await form_generator.get_form_request_by_id(session, form_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Formularz nie istnieje.",
        )

    workflow_case = await get_form_workflow_case(session, form_request_id=item.id)
    if workflow_case is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Brak zapisanej sprawy workflow dla formularza.",
        )

    workflow_case = await clear_form_workflow_delivery(
        session,
        workflow_case=workflow_case,
        updated_by=admin_user.id,
    )
    workflow_devices = await list_form_workflow_devices(session, workflow_case_id=workflow_case.id)
    await record_audit(
        session,
        user_id=admin_user.id,
        action="contracts_flow_delivery_delete",
        client_ip=admin_session.client_ip,
        payload={
            "form_request_id": item.id,
            "workflow_case_id": workflow_case.id,
        },
    )
    await session.commit()
    return {
        "ok": True,
        "message": "Usunieto dane dowozu.",
        "workflow": serialize_workflow_case(workflow_case, workflow_devices),
    }


@router.get("/delivery/schedule", summary="Harmonogram dowozow FLOW")
async def contracts_delivery_schedule(
    day_from: date | None = Query(default=None),  # noqa: B008
    day_to: date | None = Query(default=None),  # noqa: B008
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Zwraca harmonogram dowozow dla spraw FLOW w wybranym zakresie dat."""
    _, admin_user = admin_context
    if admin_user.role not in {"admin", "operator"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operacja wymaga roli administratora lub operatora.",
        )
    if not await section_permissions.user_has_section(session, admin_user, "generator"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Konto nie ma uprawnien do modulu obslugi umow.",
        )

    try:
        resolved_from, resolved_to = _normalize_schedule_range(day_from, day_to)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    stmt = (
        select(FormWorkflowCase, FormRequest)
        .join(FormRequest, FormRequest.id == FormWorkflowCase.form_request_id)
        .where(
            FormWorkflowCase.delivery_date.is_not(None),
            FormWorkflowCase.delivery_date >= resolved_from,
            FormWorkflowCase.delivery_date <= resolved_to,
        )
        .order_by(FormWorkflowCase.delivery_date.asc(), FormWorkflowCase.id.asc())
    )
    rows = (await session.execute(stmt)).all()

    items: list[dict] = []
    for workflow_case, form in rows:
        label = _delivery_label(workflow_case.delivery_date, workflow_case.delivery_time_window)
        items.append(
            {
                "workflow_case_id": workflow_case.id,
                "form_request_id": workflow_case.form_request_id,
                "delivery_date": (
                    workflow_case.delivery_date.isoformat() if workflow_case.delivery_date else None
                ),
                "delivery_time_window": workflow_case.delivery_time_window,
                "delivery_contact_name": workflow_case.delivery_contact_name,
                "delivery_contact_phone": workflow_case.delivery_contact_phone,
                "delivery_notes": workflow_case.delivery_notes,
                "delivery_label": label,
                "customer_name": _schedule_customer_name(workflow_case, form),
                "business_status": workflow_case.business_status,
                "proforma_number": workflow_case.proforma_number,
            }
        )

    return {
        "ok": True,
        "day_from": resolved_from.isoformat(),
        "day_to": resolved_to.isoformat(),
        "items": items,
    }


@router.post(
    "/delivery/{workflow_case_id}/move", summary="Przenies wpis harmonogramu na inny dzien"
)
async def contracts_delivery_move(
    workflow_case_id: int,
    payload: WorkflowDeliveryMoveRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Przenosi wpis harmonogramu dowozu na inny dzien."""
    admin_session, admin_user = admin_context
    if admin_user.role not in {"admin", "operator"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operacja wymaga roli administratora lub operatora.",
        )
    if not await section_permissions.user_has_section(session, admin_user, "generator"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Konto nie ma uprawnien do modulu obslugi umow.",
        )

    workflow_case = await session.get(FormWorkflowCase, workflow_case_id)
    if workflow_case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Sprawa workflow nie istnieje.",
        )
    if workflow_case.delivery_date is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Brak zapisanego terminu dowozu do przeniesienia.",
        )

    workflow_case.delivery_date = payload.delivery_date
    workflow_case.updated_by = admin_user.id
    workflow_case.updated_at = datetime.now(UTC)
    await record_audit(
        session,
        user_id=admin_user.id,
        action="contracts_flow_delivery_move",
        client_ip=admin_session.client_ip,
        payload={
            "workflow_case_id": workflow_case.id,
            "form_request_id": workflow_case.form_request_id,
            "delivery_date": payload.delivery_date.isoformat(),
        },
    )
    await session.commit()
    return {
        "ok": True,
        "message": "Przeniesiono wpis harmonogramu.",
        "workflow_case_id": workflow_case.id,
        "delivery_date": payload.delivery_date.isoformat(),
    }


@router.delete("/delivery/{workflow_case_id}", summary="Usun wpis harmonogramu dowozu")
async def contracts_delivery_delete(
    workflow_case_id: int,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Usuwa wpis harmonogramu dowozu dla wskazanej sprawy workflow."""
    admin_session, admin_user = admin_context
    if admin_user.role not in {"admin", "operator"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operacja wymaga roli administratora lub operatora.",
        )
    if not await section_permissions.user_has_section(session, admin_user, "generator"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Konto nie ma uprawnien do modulu obslugi umow.",
        )

    workflow_case = await session.get(FormWorkflowCase, workflow_case_id)
    if workflow_case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Sprawa workflow nie istnieje.",
        )

    workflow_case = await clear_form_workflow_delivery(
        session,
        workflow_case=workflow_case,
        updated_by=admin_user.id,
    )
    await record_audit(
        session,
        user_id=admin_user.id,
        action="contracts_flow_delivery_delete_by_case",
        client_ip=admin_session.client_ip,
        payload={
            "workflow_case_id": workflow_case.id,
            "form_request_id": workflow_case.form_request_id,
        },
    )
    await session.commit()
    return {"ok": True, "message": "Usunieto wpis harmonogramu."}


@router.post("/forms/{form_id}/workflow/client", summary="Zapisz klienta w sprawie workflow")
async def contracts_form_workflow_client(
    form_id: int,
    payload: WorkflowClientRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Tworzy albo potwierdza klienta w Menadzerze Serwisu i zapisuje stan sprawy."""
    admin_session, admin_user = admin_context
    if admin_user.role not in {"admin", "operator"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operacja wymaga roli administratora lub operatora.",
        )
    if not await section_permissions.user_has_section(session, admin_user, "generator"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Konto nie ma uprawnien do modulu obslugi umow.",
        )

    from app.services import form_generator

    item = await form_generator.get_form_request_by_id(session, form_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Formularz nie istnieje.",
        )
    if item.status != "SUBMITTED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Workflow jest dostepny tylko dla formularzy ze statusem SUBMITTED.",
        )

    submitted_payload, _ = form_generator.decode_submitted_payload(item)
    form_payload = submitted_payload or {}
    workflow_case = await get_or_create_form_workflow_case(
        session,
        form=item,
        user_id=admin_user.id,
        payload_snapshot=form_payload,
    )

    nip = normalize_nip(str(form_payload.get("company_nip") or ""))
    if not nip:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Formularz nie zawiera NIP klienta.",
        )

    match = await asyncio.to_thread(find_client_in_firebird, nip)
    if match.error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Blad odczytu klienta z Firebird: {match.error}",
        )

    created = False
    firebird_status = "linked"
    if not match.found:
        enabled, reason = firebird_writes_enabled()
        if not enabled:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=reason or "Zapis do Firebird jest zablokowany.",
            )
        try:
            result = await asyncio.to_thread(
                create_client_from_submitted_payload,
                form_payload,
                source_name=f"CTIP formularz {item.id}",
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
        created = result.created
        match = result.match
        firebird_status = "created" if result.created else "linked"

    if not match.id_klient:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Nie udalo sie ustalic ID klienta w Menadzerze Serwisu.",
        )

    workflow_case = await set_form_workflow_client(
        session,
        workflow_case=workflow_case,
        firebird_client_id=match.id_klient,
        firebird_client_status=firebird_status,
        client_mode=payload.mode,
        payload_snapshot=form_payload,
        updated_by=admin_user.id,
    )
    sync_time = datetime.now(UTC)
    item.ms_status = form_generator.build_ms_status_message(
        state="CREATED" if created else "LINKED",
        event_at=sync_time,
        client_id=match.id_klient,
        automatic=False,
    )
    item.updated_at = sync_time
    workflow_devices = await list_form_workflow_devices(session, workflow_case_id=workflow_case.id)

    await record_audit(
        session,
        user_id=admin_user.id,
        action="contracts_flow_client_save",
        client_ip=admin_session.client_ip,
        payload={
            "form_request_id": item.id,
            "workflow_case_id": workflow_case.id,
            "created": created,
            "firebird_client_id": match.id_klient,
            "mode": payload.mode,
        },
    )
    await session.commit()

    return {
        "ok": True,
        "message": (
            f"Klient jest gotowy w Menadzerze Serwisu: ID {match.id_klient}."
            if not created
            else f"Utworzono klienta w Menadzerze Serwisu: ID {match.id_klient}."
        ),
        "created": created,
        "workflow": serialize_workflow_case(workflow_case, workflow_devices),
        "id_klient": match.id_klient,
    }


@router.post("/forms/{form_id}/workflow/devices", summary="Zapisz urzadzenia w sprawie workflow")
async def contracts_form_workflow_devices(
    form_id: int,
    payload: WorkflowDevicesRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Zapisuje wybór urządzeń dla wskazanego formularza wyłącznie po stronie CTIP."""
    admin_session, admin_user = admin_context
    if admin_user.role not in {"admin", "operator"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operacja wymaga roli administratora lub operatora.",
        )
    if not await section_permissions.user_has_section(session, admin_user, "generator"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Konto nie ma uprawnien do modulu obslugi umow.",
        )

    from app.services import form_generator

    item = await form_generator.get_form_request_by_id(session, form_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Formularz nie istnieje.",
        )
    if item.status != "SUBMITTED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Workflow jest dostepny tylko dla formularzy ze statusem SUBMITTED.",
        )

    submitted_payload, _ = form_generator.decode_submitted_payload(item)
    form_payload = submitted_payload or {}
    workflow_case = await get_or_create_form_workflow_case(
        session,
        form=item,
        user_id=admin_user.id,
        payload_snapshot=form_payload,
    )

    selected_payloads = _build_selected_device_payloads(payload)
    selected_rows = [int(item["row"]) for item in selected_payloads]
    sheet_rows = {
        int(item["row"]): item for item in await asyncio.to_thread(load_devices_from_sheet)
    }
    missing_rows = [row for row in selected_rows if row not in sheet_rows]
    if missing_rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Nie znaleziono w arkuszu Urzadzenia wierszy: {', '.join(map(str, missing_rows))}.",
        )

    selected_devices = []
    for selected_item in selected_payloads:
        row_number = int(selected_item["row"])
        sheet_device = dict(sheet_rows[row_number])
        sheet_device["price_net"] = selected_item["price_net"]
        sheet_device["price_gross"] = selected_item["price_gross"]
        selected_devices.append(sheet_device)
    workflow_devices = await replace_form_workflow_devices(
        session,
        workflow_case=workflow_case,
        selected_devices=selected_devices,
        updated_by=admin_user.id,
    )
    await record_audit(
        session,
        user_id=admin_user.id,
        action="contracts_flow_devices_save",
        client_ip=admin_session.client_ip,
        payload={
            "form_request_id": item.id,
            "workflow_case_id": workflow_case.id,
            "rows": selected_rows,
            "prices": [
                {
                    "row": int(selected_item["row"]),
                    "price_net": selected_item["price_net"],
                    "price_gross": selected_item["price_gross"],
                }
                for selected_item in selected_payloads
            ],
        },
    )
    await session.commit()

    return {
        "ok": True,
        "message": (
            "Wybor urzadzen zapisany po stronie CTIP."
            if workflow_devices
            else "Usunieto powiazane urzadzenia ze sprawy CTIP."
        ),
        "workflow": serialize_workflow_case(workflow_case, workflow_devices),
        "selected_rows": selected_rows,
    }


@router.post("/forms/{form_id}/workflow/status", summary="Zapisz status biznesowy sprawy workflow")
async def contracts_form_workflow_status(
    form_id: int,
    payload: WorkflowStatusRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Zapisuje recznie ustawiany status biznesowy sprawy FLOW."""
    admin_session, admin_user = admin_context
    if admin_user.role not in {"admin", "operator"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operacja wymaga roli administratora lub operatora.",
        )
    if not await section_permissions.user_has_section(session, admin_user, "generator"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Konto nie ma uprawnien do modulu obslugi umow.",
        )

    from app.services import form_generator

    item = await form_generator.get_form_request_by_id(session, form_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Formularz nie istnieje.",
        )
    if item.status != "SUBMITTED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Workflow jest dostepny tylko dla formularzy ze statusem SUBMITTED.",
        )

    submitted_payload, _ = form_generator.decode_submitted_payload(item)
    workflow_case = await get_or_create_form_workflow_case(
        session,
        form=item,
        user_id=admin_user.id,
        payload_snapshot=submitted_payload or {},
    )
    workflow_case = await set_form_workflow_business_status(
        session,
        workflow_case=workflow_case,
        business_status=payload.business_status,
        updated_by=admin_user.id,
    )
    workflow_devices = await list_form_workflow_devices(session, workflow_case_id=workflow_case.id)
    await record_audit(
        session,
        user_id=admin_user.id,
        action="contracts_flow_status_save",
        client_ip=admin_session.client_ip,
        payload={
            "form_request_id": item.id,
            "workflow_case_id": workflow_case.id,
            "business_status": payload.business_status,
        },
    )
    await session.commit()
    return {
        "ok": True,
        "message": "Zapisano status sprawy FLOW.",
        "workflow": serialize_workflow_case(workflow_case, workflow_devices),
    }


@router.post("/forms/{form_id}/workflow/proforma", summary="Utworz proforme dla sprawy workflow")
async def contracts_form_workflow_proforma(
    form_id: int,
    payload: WorkflowProformaRequest | None = None,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Tworzy proforme w lokalnej Firebird dla formularza SUBMITTED."""
    payload_data = payload or WorkflowProformaRequest()
    admin_session, admin_user = admin_context
    if admin_user.role not in {"admin", "operator"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operacja wymaga roli administratora lub operatora.",
        )
    if not await section_permissions.user_has_section(session, admin_user, "generator"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Konto nie ma uprawnien do modulu obslugi umow.",
        )

    from app.services import form_generator

    item = await form_generator.get_form_request_by_id(session, form_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Formularz nie istnieje.",
        )
    if item.status != "SUBMITTED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Workflow jest dostepny tylko dla formularzy ze statusem SUBMITTED.",
        )

    workflow_case = await get_form_workflow_case(session, form_request_id=item.id)
    if workflow_case is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Najpierw zapisz klienta i wybierz urzadzenia dla formularza.",
        )

    workflow_devices = await list_form_workflow_devices(session, workflow_case_id=workflow_case.id)
    if not workflow_case.firebird_client_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Najpierw zapisz klienta w Menadzerze Serwisu.",
        )
    if not workflow_devices:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Najpierw wybierz co najmniej jedno urzadzenie do proformy.",
        )

    if workflow_case.proforma_firebird_id and workflow_case.proforma_number:
        await session.commit()
        return {
            "ok": True,
            "created": False,
            "message": f"Proforma jest juz zapisana: {workflow_case.proforma_number}.",
            "proforma_firebird_id": workflow_case.proforma_firebird_id,
            "proforma_number": workflow_case.proforma_number,
            "preview_url": serialize_workflow_case(workflow_case, workflow_devices).get(
                "proforma_preview_url"
            ),
            "workflow": serialize_workflow_case(workflow_case, workflow_devices),
        }

    issuer_name = (
        " ".join(
            part.strip()
            for part in [admin_user.first_name or "", admin_user.last_name or ""]
            if part and part.strip()
        ).strip()
        or admin_user.email
    )
    selected_devices = [
        device.snapshot
        or {
            "row": device.source_row,
            "producer": device.producer,
            "model": device.model,
            "serial": device.serial,
            "ewidencja": device.ewidencja,
            "status": device.device_status,
            "reservation_status": device.reservation_status,
            "price": device.price,
            "price_net": device.price_net,
            "price_gross": device.price_gross,
            "ms_id_maszyna": device.firebird_machine_id,
            "ms_id_klient": device.firebird_client_id,
        }
        for device in workflow_devices
    ]

    try:
        recipient_client_id, recipient_label = await _resolve_proforma_recipient_client_id(
            for_bank=payload_data.for_bank,
            workflow_client_id=int(workflow_case.firebird_client_id),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    try:
        result = await asyncio.to_thread(
            create_proforma_from_workflow,
            form_request_id=item.id,
            firebird_client_id=recipient_client_id,
            selected_devices=selected_devices,
            issuer_name=issuer_name,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    workflow_case = await set_form_workflow_proforma(
        session,
        workflow_case=workflow_case,
        proforma_firebird_id=result.id_faktura_table,
        proforma_number=result.document_number,
        proforma_pdf_path=result.pdf_path or result.preview_url,
        updated_by=admin_user.id,
    )
    await record_audit(
        session,
        user_id=admin_user.id,
        action="contracts_flow_proforma_create",
        client_ip=admin_session.client_ip,
        payload={
            "form_request_id": item.id,
            "workflow_case_id": workflow_case.id,
            "proforma_firebird_id": result.id_faktura_table,
            "proforma_number": result.document_number,
            "line_count": result.line_count,
            "for_bank": bool(payload_data.for_bank),
            "recipient_client_id": recipient_client_id,
        },
    )
    await session.commit()

    return {
        "ok": True,
        "created": True,
        "message": (
            "Utworzono proforme w Menadzerze Serwisu: "
            f"{result.document_number} (odbiorca: {recipient_label})."
        ),
        "proforma_firebird_id": result.id_faktura_table,
        "proforma_number": result.document_number,
        "preview_url": result.preview_url,
        "for_bank": bool(payload_data.for_bank),
        "recipient_client_id": recipient_client_id,
        "workflow": serialize_workflow_case(workflow_case, workflow_devices),
    }


@router.post("/action", summary="Uruchom akcję workflow dla umów")
async def contracts_dashboard_action(
    payload: ContractActionRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Obsługuje kliknięcie akcji w dashboardzie."""
    admin_session, admin_user = admin_context
    if admin_user.role not in {"admin", "operator"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operacja wymaga roli administratora lub operatora.",
        )
    if not await section_permissions.user_has_section(session, admin_user, "generator"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Konto nie ma uprawnien do modulu obslugi umow.",
        )

    if payload.entity == "form":
        if payload.target_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Dla akcji formularza wymagane jest target_id.",
            )

        from app.services import form_generator

        item = await form_generator.get_form_request_by_id(session, payload.target_id)
        if item is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Formularz nie istnieje.",
            )
        if item.status != "SUBMITTED":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Akcja jest dostepna tylko dla formularzy ze statusem SUBMITTED.",
            )

        submitted_payload, _ = form_generator.decode_submitted_payload(item)
        form_payload = submitted_payload or {}

        if payload.action == "utworz_klienta":
            enabled, reason = firebird_writes_enabled()
            if not enabled:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=reason or "Zapis do Firebird jest zablokowany.",
                )

            try:
                result = await asyncio.to_thread(
                    create_client_from_submitted_payload,
                    form_payload,
                    source_name=f"CTIP formularz {item.id}",
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(exc),
                ) from exc
            except RuntimeError as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=str(exc),
                ) from exc

            workflow_case = await get_or_create_form_workflow_case(
                session,
                form=item,
                user_id=admin_user.id,
                payload_snapshot=form_payload,
            )
            if result.match.id_klient:
                await set_form_workflow_client(
                    session,
                    workflow_case=workflow_case,
                    firebird_client_id=result.match.id_klient,
                    firebird_client_status="created" if result.created else "linked",
                    client_mode=WORKFLOW_CLIENT_MODE_BASIC_PROFORMA,
                    payload_snapshot=form_payload,
                    updated_by=admin_user.id,
                )
            sync_time = datetime.now(UTC)
            item.ms_status = form_generator.build_ms_status_message(
                state="CREATED" if result.created else "LINKED",
                event_at=sync_time,
                client_id=result.match.id_klient,
                automatic=False,
            )
            item.updated_at = sync_time

            await record_audit(
                session,
                user_id=admin_user.id,
                action="contracts_client_create",
                client_ip=admin_session.client_ip,
                payload={
                    "form_request_id": item.id,
                    "created": result.created,
                    "id_klient": result.match.id_klient,
                    "nip": result.match.nip
                    or normalize_nip(str(form_payload.get("company_nip") or "")),
                },
            )
            await session.commit()

            if result.created:
                message = f"Utworzono klienta w lokalnej Firebird: ID {result.match.id_klient}."
            else:
                message = f"Klient juz istnieje w Firebird: ID {result.match.id_klient}."
            return {
                "ok": True,
                "message": message,
                "id_klient": result.match.id_klient,
                "created": result.created,
            }

        if payload.action == "podlacz_klienta":
            nip = normalize_nip(str(form_payload.get("company_nip") or ""))
            if not nip:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Formularz nie zawiera NIP klienta.",
                )

            match = await asyncio.to_thread(find_client_in_firebird, nip)
            if match.error:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"Blad odczytu klienta z Firebird: {match.error}",
                )
            if not match.found:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Klient nie istnieje w Firebird. Najpierw utworz klienta.",
                )

            workflow_case = await get_or_create_form_workflow_case(
                session,
                form=item,
                user_id=admin_user.id,
                payload_snapshot=form_payload,
            )
            if match.id_klient:
                await set_form_workflow_client(
                    session,
                    workflow_case=workflow_case,
                    firebird_client_id=match.id_klient,
                    firebird_client_status="linked",
                    client_mode=WORKFLOW_CLIENT_MODE_BASIC_PROFORMA,
                    payload_snapshot=form_payload,
                    updated_by=admin_user.id,
                )
            sync_time = datetime.now(UTC)
            item.ms_status = form_generator.build_ms_status_message(
                state="LINKED",
                event_at=sync_time,
                client_id=match.id_klient,
                automatic=False,
            )
            item.updated_at = sync_time

            await record_audit(
                session,
                user_id=admin_user.id,
                action="contracts_client_link_preview",
                client_ip=admin_session.client_ip,
                payload={
                    "form_request_id": item.id,
                    "id_klient": match.id_klient,
                    "nip": nip,
                },
            )
            await session.commit()
            return {
                "ok": True,
                "message": (
                    f"Potwierdzono klienta w Firebird: ID {match.id_klient}. "
                    "Trwale mapowanie formularza do Firebird nie jest jeszcze zapisane po stronie CTIP."
                ),
                "id_klient": match.id_klient,
                "created": False,
            }

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Nieznana akcja formularza: {payload.action}",
        )

    if payload.entity == "device":
        if payload.row is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Dla akcji urzadzenia wymagany jest numer wiersza arkusza.",
            )
        target_text = f"wiersz urzadzenia {payload.row}"
        device = await asyncio.to_thread(load_device_from_sheet_row, payload.row)
        if device is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Nie znaleziono {target_text} w arkuszu Urzadzenia.",
            )

        if payload.action in {"synchronizuj", "podlacz"}:
            enabled, reason = firebird_writes_enabled()
            if not enabled:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=reason or "Zapis do Firebird jest zablokowany.",
                )
            try:
                result = await asyncio.to_thread(
                    synchronize_device_from_sheet_row,
                    payload.row,
                    kto="CTIP",
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(exc),
                ) from exc
            except RuntimeError as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=str(exc),
                ) from exc

            await record_audit(
                session,
                user_id=admin_user.id,
                action="contracts_device_sync",
                client_ip=admin_session.client_ip,
                payload={
                    "row": payload.row,
                    "serial": result.serial,
                    "ewidencja": result.ewidencja,
                    "machine_id": result.machine_id,
                    "machine_created": result.machine_created,
                    "warehouse_id": result.warehouse_id,
                    "warehouse_created": result.warehouse_created,
                    "model_id": result.model_id,
                },
            )
            await session.commit()
            return {
                "ok": True,
                "message": (
                    f"Zsynchronizowano {target_text}: "
                    f"MASZYNA ID {result.machine_id} "
                    f"({'utworzono' if result.machine_created else 'istnialo'}), "
                    f"MAGAZYN ID {result.warehouse_id} "
                    f"({'utworzono' if result.warehouse_created else 'istnialo'})."
                ),
                "machine_id": result.machine_id,
                "machine_created": result.machine_created,
                "warehouse_id": result.warehouse_id,
                "warehouse_created": result.warehouse_created,
                "model_id": result.model_id,
            }

        if payload.action == "do_weryfikacji":
            await record_audit(
                session,
                user_id=admin_user.id,
                action="contracts_device_review_mark",
                client_ip=admin_session.client_ip,
                payload={"row": payload.row},
            )
            await session.commit()
            return {
                "ok": True,
                "message": f"{target_text.capitalize()} pozostaje w recznej weryfikacji.",
            }
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Akcja '{payload.action}' dla {target_text} nie jest jeszcze zaimplementowana "
                "w module Obsluga umow."
            ),
        )

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Nieprawidlowy typ encji akcji.",
    )


__all__ = ["router"]
