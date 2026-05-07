"""Obsługa spraw workflow dla formularzy w module FLOW."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FormRequest, FormWorkflowCase, FormWorkflowDevice

WORKFLOW_STAGE_FORM_SUBMITTED = "FORM_SUBMITTED"
WORKFLOW_STAGE_CLIENT_READY = "CLIENT_READY"
WORKFLOW_STAGE_DEVICES_SELECTED = "DEVICES_SELECTED"
WORKFLOW_STAGE_PROFORMA_CREATED = "PROFORMA_CREATED"

WORKFLOW_CLIENT_MODE_BASIC_PROFORMA = "basic_proforma"
WORKFLOW_DEVICE_SOURCE_GOOGLE_SHEET = "google_sheet"
WORKFLOW_DEVICE_SOURCE_FIREBIRD_WAREHOUSE = "firebird_magazyn_28"
WORKFLOW_DEVICE_SOURCE_FIREBIRD_SERIAL = "firebird_serial"
WORKFLOW_DEVICE_SOURCES = (
    WORKFLOW_DEVICE_SOURCE_GOOGLE_SHEET,
    WORKFLOW_DEVICE_SOURCE_FIREBIRD_WAREHOUSE,
    WORKFLOW_DEVICE_SOURCE_FIREBIRD_SERIAL,
)

WORKFLOW_BUSINESS_STATUS_DRAFT = "DRAFT"
WORKFLOW_BUSINESS_STATUS_PENDING_APPROVAL = "PENDING_APPROVAL"
WORKFLOW_BUSINESS_STATUS_APPROVED = "APPROVED"
WORKFLOW_BUSINESS_STATUS_ZEROWKA = "ZEROWKA"
WORKFLOW_BUSINESS_STATUS_REJECTED = "REJECTED"
WORKFLOW_BUSINESS_STATUS_WAITING_SIGNATURE = "WAITING_SIGNATURE"
WORKFLOW_BUSINESS_STATUS_APPROVED_ORDER = "APPROVED_ORDER"
WORKFLOW_BUSINESS_STATUS_REJECTED_GRENKE = "REJECTED_GRENKE"

WORKFLOW_LEGACY_STATUS_MAP = {
    WORKFLOW_BUSINESS_STATUS_PENDING_APPROVAL: WORKFLOW_BUSINESS_STATUS_WAITING_SIGNATURE,
    WORKFLOW_BUSINESS_STATUS_APPROVED: WORKFLOW_BUSINESS_STATUS_APPROVED_ORDER,
    WORKFLOW_BUSINESS_STATUS_REJECTED: WORKFLOW_BUSINESS_STATUS_REJECTED_GRENKE,
}

WORKFLOW_BUSINESS_STATUS_LABELS = {
    WORKFLOW_BUSINESS_STATUS_DRAFT: "Wypełniony formularz klienta",
    WORKFLOW_BUSINESS_STATUS_PENDING_APPROVAL: "Umowa GRENKE czeka na podpis",
    WORKFLOW_BUSINESS_STATUS_APPROVED: "Zgoda na realizację zamówienia",
    WORKFLOW_BUSINESS_STATUS_ZEROWKA: "Zerówka",
    WORKFLOW_BUSINESS_STATUS_REJECTED: "Odmowa GRENKE",
    WORKFLOW_BUSINESS_STATUS_WAITING_SIGNATURE: "Umowa GRENKE czeka na podpis",
    WORKFLOW_BUSINESS_STATUS_APPROVED_ORDER: "Zgoda na realizację zamówienia",
    WORKFLOW_BUSINESS_STATUS_REJECTED_GRENKE: "Odmowa GRENKE",
}

WORKFLOW_MANUAL_STATUS_OPTIONS = (
    WORKFLOW_BUSINESS_STATUS_WAITING_SIGNATURE,
    WORKFLOW_BUSINESS_STATUS_APPROVED_ORDER,
    WORKFLOW_BUSINESS_STATUS_REJECTED_GRENKE,
)


def build_workflow_proforma_preview_url(proforma_firebird_id: int | None) -> str | None:
    """Buduje URL podgladu proformy zapisanej w Firebird."""
    if not proforma_firebird_id:
        return None
    return f"/flow/proforma/{proforma_firebird_id}?variant=final"


def normalize_workflow_device_source_type(
    value: str | None,
    *,
    default: str = WORKFLOW_DEVICE_SOURCE_GOOGLE_SHEET,
) -> str:
    """Normalizuje typ źródła urządzenia do jednego z obsługiwanych wariantów."""
    normalized = str(value or "").strip()
    if normalized in WORKFLOW_DEVICE_SOURCES:
        return normalized
    return default


def build_workflow_device_key(source_type: str | None, row: Any) -> str | None:
    """Buduje stabilny klucz urządzenia workflow z typu źródła i numeru rekordu."""
    try:
        row_number = int(row) if row not in (None, "") else 0
    except (TypeError, ValueError):
        row_number = 0
    if row_number <= 0:
        return None
    normalized_source = normalize_workflow_device_source_type(source_type)
    return f"{normalized_source}:{row_number}"


def build_workflow_proforma_pdf_url(proforma_firebird_id: int | None) -> str | None:
    """Buduje URL backendowego pliku PDF proformy."""
    if not proforma_firebird_id:
        return None
    return f"/flow/proforma/{proforma_firebird_id}/pdf"


def _resolve_proforma_preview_url(workflow_case: FormWorkflowCase | None) -> str | None:
    if workflow_case is None:
        return None
    stored_path = str(workflow_case.proforma_pdf_path or "").strip()
    if stored_path.startswith("/"):
        return stored_path
    if stored_path.lower().endswith(".pdf"):
        return build_workflow_proforma_pdf_url(workflow_case.proforma_firebird_id)
    return build_workflow_proforma_preview_url(workflow_case.proforma_firebird_id)


def workflow_business_status_label(value: str | None) -> str:
    """Zwraca czytelna etykiete statusu biznesowego sprawy."""
    normalized = normalize_workflow_business_status(value)
    return WORKFLOW_BUSINESS_STATUS_LABELS.get(
        normalized,
        str(value or WORKFLOW_BUSINESS_STATUS_DRAFT),
    )


def build_workflow_business_status_options() -> list[dict[str, str]]:
    """Zwraca ręczne opcje statusu GRENKE do UI."""
    return [
        {"value": key, "label": WORKFLOW_BUSINESS_STATUS_LABELS[key]}
        for key in WORKFLOW_MANUAL_STATUS_OPTIONS
    ]


def normalize_workflow_business_status(value: str | None) -> str:
    """Mapuje stare wartości statusów na bieżący kanon używany przez GenForm."""
    raw = str(value or WORKFLOW_BUSINESS_STATUS_DRAFT).strip() or WORKFLOW_BUSINESS_STATUS_DRAFT
    return WORKFLOW_LEGACY_STATUS_MAP.get(raw, raw)


def append_workflow_status_history(
    workflow_case: FormWorkflowCase,
    *,
    status_value: str,
    status_source: str,
    changed_at: datetime,
    note: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Dopisuje wpis historii statusów bez nadpisywania istniejących danych sprawy."""
    history = workflow_case.status_history if isinstance(workflow_case.status_history, list) else []
    event: dict[str, Any] = {
        "status": status_value,
        "label": workflow_business_status_label(status_value),
        "source": status_source,
        "changed_at": changed_at.isoformat(),
    }
    if note:
        event["note"] = str(note).strip()
    if metadata:
        event["metadata"] = metadata
    workflow_case.status_history = [*history, event]


def build_client_preview(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Buduje krótki podgląd danych klienta do akceptacji w FLOW."""

    def _value(name: str) -> str:
        raw = payload.get(name)
        if raw is None:
            return ""
        return str(raw).strip()

    preview = [
        {"label": "Nazwa firmy", "value": _value("company_name")},
        {"label": "NIP", "value": _value("company_nip")},
        {"label": "Telefon firmy", "value": _value("company_phone")},
        {"label": "E-mail firmy", "value": _value("company_email")},
        {"label": "E-mail do faktur", "value": _value("billing_email")},
        {"label": "Adres rejestrowy", "value": _build_address(payload, "registered")},
    ]

    if not payload.get("correspondence_same_as_registered"):
        preview.append(
            {
                "label": "Adres korespondencyjny",
                "value": _build_address(payload, "correspondence"),
            }
        )

    representatives = payload.get("representatives")
    if isinstance(representatives, list):
        for index, representative in enumerate(representatives, start=1):
            if not isinstance(representative, dict):
                continue
            full_name = _representative_full_name(representative)
            if full_name:
                preview.append(
                    {
                        "label": f"Reprezentant {index}",
                        "value": full_name,
                    }
                )

    return [item for item in preview if item["value"]]


def derive_workflow_stage(
    *,
    firebird_client_id: int | None,
    devices_count: int,
    proforma_number: str | None,
    proforma_firebird_id: int | None,
) -> str:
    """Wylicza etap sprawy na podstawie zapisanych powiązań."""
    if proforma_number or proforma_firebird_id:
        return WORKFLOW_STAGE_PROFORMA_CREATED
    if devices_count > 0:
        return WORKFLOW_STAGE_DEVICES_SELECTED
    if firebird_client_id is not None:
        return WORKFLOW_STAGE_CLIENT_READY
    return WORKFLOW_STAGE_FORM_SUBMITTED


def build_sales_packet(
    workflow_case: FormWorkflowCase | None,
    devices: list[FormWorkflowDevice] | None = None,
) -> dict[str, Any]:
    """Buduje dane do przekazania handlowcowi dla konkretnej sprawy."""
    payload = (
        workflow_case.client_payload_snapshot
        if workflow_case and isinstance(workflow_case.client_payload_snapshot, dict)
        else {}
    )
    selected_devices = devices or []

    representatives_output: list[dict[str, str]] = []
    representatives = payload.get("representatives")
    if isinstance(representatives, list):
        for index, representative in enumerate(representatives, start=1):
            if not isinstance(representative, dict):
                continue
            representatives_output.append(
                {
                    "label": f"Reprezentant {index}",
                    "value": _representative_summary(representative),
                }
            )

    device_rows = []
    for device in selected_devices:
        snapshot = device.snapshot if isinstance(device.snapshot, dict) else {}
        device_rows.append(
            {
                "row": device.source_row,
                "producer": device.producer or "",
                "model": device.model or "",
                "serial": device.serial or "",
                "ewidencja": device.ewidencja or "",
                "index": str(snapshot.get("index") or device.ewidencja or "").strip(),
                "name": str(
                    snapshot.get("name") or snapshot.get("description") or device.model or ""
                ).strip(),
                "available_quantity": str(snapshot.get("available_quantity") or "").strip(),
                "price_net": _normalize_price_text(device.price_net),
                "price_gross": _normalize_price_text(device.price_gross),
            }
        )

    client_fields = [
        {"label": "Nazwa firmy", "value": _payload_text(payload, "company_name")},
        {"label": "NIP", "value": _payload_text(payload, "company_nip")},
        {"label": "Telefon firmy", "value": _payload_text(payload, "company_phone")},
        {"label": "E-mail firmy", "value": _payload_text(payload, "company_email")},
        {"label": "E-mail do faktur", "value": _payload_text(payload, "billing_email")},
        {"label": "Adres rejestrowy", "value": _build_address(payload, "registered")},
        {
            "label": "Adres korespondencyjny",
            "value": (
                _build_address(payload, "registered")
                if payload.get("correspondence_same_as_registered")
                else _build_address(payload, "correspondence")
            ),
        },
    ]

    preview_url = _resolve_proforma_preview_url(workflow_case)

    return {
        "client_fields": [item for item in client_fields if item["value"]],
        "representatives": [item for item in representatives_output if item["value"]],
        "devices": device_rows,
        "proforma_number": workflow_case.proforma_number if workflow_case else None,
        "proforma_preview_url": preview_url,
    }


def serialize_workflow_case(
    workflow_case: FormWorkflowCase | None,
    devices: list[FormWorkflowDevice] | None = None,
) -> dict[str, Any]:
    """Zwraca czytelny stan sprawy workflow do API."""
    case_devices = devices or []
    if workflow_case is None:
        return {
            "exists": False,
            "id": None,
            "stage": WORKFLOW_STAGE_FORM_SUBMITTED,
            "business_status": WORKFLOW_BUSINESS_STATUS_DRAFT,
            "business_status_label": workflow_business_status_label(WORKFLOW_BUSINESS_STATUS_DRAFT),
            "business_status_canonical": WORKFLOW_BUSINESS_STATUS_DRAFT,
            "signature_deadline_at": None,
            "resources_release_due_at": None,
            "resources_released_at": None,
            "status_changed_at": None,
            "status_source": None,
            "status_history": [],
            "firebird_client_id": None,
            "firebird_client_status": None,
            "client_mode": None,
            "devices_selected_count": 0,
            "selected_device_rows": [],
            "selected_devices": [],
            "proforma_firebird_id": None,
            "proforma_number": None,
            "proforma_pdf_path": None,
            "proforma_preview_url": None,
            "delivery_planned": False,
            "delivery_date": None,
            "delivery_time_window": None,
            "delivery_contact_name": None,
            "delivery_contact_phone": None,
            "delivery_notes": None,
            "delivery_label": None,
            "sheet_sync": _build_sheet_sync_state([]),
        }

    preview_url = _resolve_proforma_preview_url(workflow_case)
    delivery_label = _build_delivery_label(
        workflow_case.delivery_date,
        workflow_case.delivery_time_window,
    )
    return {
        "exists": True,
        "id": workflow_case.id,
        "stage": derive_workflow_stage(
            firebird_client_id=workflow_case.firebird_client_id,
            devices_count=len(case_devices),
            proforma_number=workflow_case.proforma_number,
            proforma_firebird_id=workflow_case.proforma_firebird_id,
        ),
        "business_status": normalize_workflow_business_status(workflow_case.business_status),
        "business_status_raw": workflow_case.business_status,
        "business_status_canonical": normalize_workflow_business_status(
            workflow_case.business_status
        ),
        "business_status_label": workflow_business_status_label(workflow_case.business_status),
        "signature_deadline_at": (
            workflow_case.signature_deadline_at.isoformat()
            if workflow_case.signature_deadline_at
            else None
        ),
        "resources_release_due_at": (
            workflow_case.resources_release_due_at.isoformat()
            if workflow_case.resources_release_due_at
            else None
        ),
        "resources_released_at": (
            workflow_case.resources_released_at.isoformat()
            if workflow_case.resources_released_at
            else None
        ),
        "status_changed_at": (
            workflow_case.status_changed_at.isoformat() if workflow_case.status_changed_at else None
        ),
        "status_source": workflow_case.status_source,
        "status_history": (
            workflow_case.status_history if isinstance(workflow_case.status_history, list) else []
        ),
        "firebird_client_id": workflow_case.firebird_client_id,
        "firebird_client_status": workflow_case.firebird_client_status,
        "client_mode": workflow_case.client_mode,
        "devices_selected_count": len(case_devices),
        "selected_device_rows": [
            device.source_row for device in case_devices if device.source_row is not None
        ],
        "selected_devices": [
            {
                "row": device.source_row,
                "source_type": normalize_workflow_device_source_type(device.source_type),
                "source_key": build_workflow_device_key(device.source_type, device.source_row),
            }
            for device in case_devices
            if device.source_row is not None
        ],
        "proforma_firebird_id": workflow_case.proforma_firebird_id,
        "proforma_number": workflow_case.proforma_number,
        "proforma_pdf_path": workflow_case.proforma_pdf_path,
        "proforma_preview_url": preview_url,
        "delivery_planned": workflow_case.delivery_date is not None,
        "delivery_date": (
            workflow_case.delivery_date.isoformat() if workflow_case.delivery_date else None
        ),
        "delivery_time_window": workflow_case.delivery_time_window,
        "delivery_contact_name": workflow_case.delivery_contact_name,
        "delivery_contact_phone": workflow_case.delivery_contact_phone,
        "delivery_notes": workflow_case.delivery_notes,
        "delivery_label": delivery_label,
        "sheet_sync": _build_sheet_sync_state(case_devices),
    }


async def get_form_workflow_case(
    session: AsyncSession, *, form_request_id: int
) -> FormWorkflowCase | None:
    """Zwraca sprawę workflow powiązaną z formularzem."""
    stmt = select(FormWorkflowCase).where(FormWorkflowCase.form_request_id == form_request_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_form_workflow_devices(
    session: AsyncSession, *, workflow_case_id: int
) -> list[FormWorkflowDevice]:
    """Zwraca urządzenia zapisane po stronie CTIP dla wskazanej sprawy."""
    stmt = (
        select(FormWorkflowDevice)
        .where(FormWorkflowDevice.workflow_case_id == workflow_case_id)
        .order_by(FormWorkflowDevice.source_row.asc(), FormWorkflowDevice.id.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_or_create_form_workflow_case(
    session: AsyncSession,
    *,
    form: FormRequest,
    user_id: int | None,
    payload_snapshot: dict[str, Any] | None = None,
) -> FormWorkflowCase:
    """Pobiera lub tworzy sprawę workflow dla formularza."""
    existing = await get_form_workflow_case(session, form_request_id=form.id)
    if existing is not None:
        if payload_snapshot and not existing.client_payload_snapshot:
            existing.client_payload_snapshot = payload_snapshot
            existing.updated_at = datetime.now(UTC)
            existing.updated_by = user_id
        return existing

    workflow_case = FormWorkflowCase(
        form_request_id=form.id,
        created_by=user_id,
        updated_by=user_id,
        stage=WORKFLOW_STAGE_FORM_SUBMITTED,
        business_status=WORKFLOW_BUSINESS_STATUS_DRAFT,
        client_payload_snapshot=payload_snapshot,
    )
    session.add(workflow_case)
    await session.flush()
    return workflow_case


async def set_form_workflow_client(
    session: AsyncSession,
    *,
    workflow_case: FormWorkflowCase,
    firebird_client_id: int,
    firebird_client_status: str,
    client_mode: str,
    payload_snapshot: dict[str, Any] | None,
    updated_by: int | None,
) -> FormWorkflowCase:
    """Aktualizuje zapis klienta w sprawie workflow."""
    devices = await list_form_workflow_devices(session, workflow_case_id=workflow_case.id)
    workflow_case.firebird_client_id = firebird_client_id
    workflow_case.firebird_client_status = firebird_client_status
    workflow_case.client_mode = client_mode
    workflow_case.client_payload_snapshot = payload_snapshot
    workflow_case.updated_by = updated_by
    workflow_case.updated_at = datetime.now(UTC)
    workflow_case.stage = derive_workflow_stage(
        firebird_client_id=workflow_case.firebird_client_id,
        devices_count=len(devices),
        proforma_number=workflow_case.proforma_number,
        proforma_firebird_id=workflow_case.proforma_firebird_id,
    )
    await session.flush()
    return workflow_case


async def replace_form_workflow_devices(
    session: AsyncSession,
    *,
    workflow_case: FormWorkflowCase,
    selected_devices: Iterable[dict[str, Any]],
    updated_by: int | None,
) -> list[FormWorkflowDevice]:
    """Podmienia zestaw urządzeń wybranych do sprawy formularza."""
    await session.execute(
        delete(FormWorkflowDevice).where(FormWorkflowDevice.workflow_case_id == workflow_case.id)
    )

    new_rows: list[FormWorkflowDevice] = []
    for device in selected_devices:
        row_value = device.get("row")
        try:
            source_row = int(row_value) if row_value not in (None, "") else None
        except (TypeError, ValueError):
            source_row = None

        mapped = FormWorkflowDevice(
            workflow_case_id=workflow_case.id,
            source_type=normalize_workflow_device_source_type(device.get("source_type")),
            source_row=source_row,
            producer=str(device.get("producer") or "").strip() or None,
            model=str(device.get("model") or "").strip() or None,
            serial=str(device.get("serial") or "").strip() or None,
            ewidencja=str(device.get("ewidencja") or "").strip() or None,
            device_status=str(device.get("status") or "").strip() or None,
            reservation_status=str(device.get("reservation_status") or "").strip() or None,
            price=str(device.get("price") or "").strip() or None,
            price_net=_normalize_price_text(device.get("price_net")),
            price_gross=_normalize_price_text(device.get("price_gross")),
            firebird_machine_id=_coerce_int(device.get("ms_id_maszyna")),
            firebird_client_id=_coerce_int(device.get("ms_id_klient")),
            snapshot=device,
        )
        new_rows.append(mapped)
        session.add(mapped)

    workflow_case.updated_by = updated_by
    workflow_case.updated_at = datetime.now(UTC)
    workflow_case.stage = derive_workflow_stage(
        firebird_client_id=workflow_case.firebird_client_id,
        devices_count=len(new_rows),
        proforma_number=workflow_case.proforma_number,
        proforma_firebird_id=workflow_case.proforma_firebird_id,
    )
    await session.flush()
    return new_rows


async def set_form_workflow_proforma(
    session: AsyncSession,
    *,
    workflow_case: FormWorkflowCase,
    proforma_firebird_id: int,
    proforma_number: str,
    proforma_pdf_path: str | None,
    updated_by: int | None,
) -> FormWorkflowCase:
    """Zapisuje dane proformy dla sprawy workflow."""
    devices = await list_form_workflow_devices(session, workflow_case_id=workflow_case.id)
    workflow_case.proforma_firebird_id = proforma_firebird_id
    workflow_case.proforma_number = proforma_number
    workflow_case.proforma_pdf_path = proforma_pdf_path
    if (
        normalize_workflow_business_status(workflow_case.business_status)
        == WORKFLOW_BUSINESS_STATUS_DRAFT
    ):
        now = datetime.now(UTC)
        workflow_case.business_status = WORKFLOW_BUSINESS_STATUS_WAITING_SIGNATURE
        workflow_case.signature_deadline_at = now + timedelta(days=7)
        workflow_case.status_changed_at = now
        workflow_case.status_source = "system_proforma"
        append_workflow_status_history(
            workflow_case,
            status_value=WORKFLOW_BUSINESS_STATUS_WAITING_SIGNATURE,
            status_source="system_proforma",
            changed_at=now,
            metadata={"proforma_number": proforma_number},
        )
    workflow_case.updated_by = updated_by
    workflow_case.updated_at = datetime.now(UTC)
    workflow_case.stage = derive_workflow_stage(
        firebird_client_id=workflow_case.firebird_client_id,
        devices_count=len(devices),
        proforma_number=workflow_case.proforma_number,
        proforma_firebird_id=workflow_case.proforma_firebird_id,
    )
    await session.flush()
    return workflow_case


async def clear_form_workflow_proforma(
    session: AsyncSession,
    *,
    workflow_case: FormWorkflowCase,
    updated_by: int | None,
) -> FormWorkflowCase:
    """Usuwa informacje o proformie zapisane w sprawie workflow."""
    devices = await list_form_workflow_devices(session, workflow_case_id=workflow_case.id)
    workflow_case.proforma_firebird_id = None
    workflow_case.proforma_number = None
    workflow_case.proforma_pdf_path = None
    workflow_case.updated_by = updated_by
    workflow_case.updated_at = datetime.now(UTC)
    workflow_case.stage = derive_workflow_stage(
        firebird_client_id=workflow_case.firebird_client_id,
        devices_count=len(devices),
        proforma_number=workflow_case.proforma_number,
        proforma_firebird_id=workflow_case.proforma_firebird_id,
    )
    await session.flush()
    return workflow_case


async def set_form_workflow_business_status(
    session: AsyncSession,
    *,
    workflow_case: FormWorkflowCase,
    business_status: str,
    updated_by: int | None,
    signature_deadline_at: datetime | None = None,
    status_source: str = "manual",
    note: str | None = None,
) -> FormWorkflowCase:
    """Zapisuje status biznesowy sprawy workflow."""
    now = datetime.now(UTC)
    normalized_status = normalize_workflow_business_status(business_status)
    workflow_case.business_status = normalized_status
    workflow_case.status_changed_at = now
    workflow_case.status_source = status_source
    if normalized_status == WORKFLOW_BUSINESS_STATUS_WAITING_SIGNATURE:
        workflow_case.signature_deadline_at = signature_deadline_at or (now + timedelta(days=7))
    if normalized_status == WORKFLOW_BUSINESS_STATUS_APPROVED_ORDER:
        workflow_case.resources_release_due_at = None
    if (
        normalized_status == WORKFLOW_BUSINESS_STATUS_REJECTED_GRENKE
        and workflow_case.resources_released_at is None
        and workflow_case.resources_release_due_at is None
    ):
        workflow_case.resources_release_due_at = now + timedelta(days=7)
    append_workflow_status_history(
        workflow_case,
        status_value=normalized_status,
        status_source=status_source,
        changed_at=now,
        note=note,
        metadata={"updated_by": updated_by} if updated_by is not None else None,
    )
    workflow_case.updated_by = updated_by
    workflow_case.updated_at = now
    await session.flush()
    return workflow_case


async def mark_workflow_resources_released(
    session: AsyncSession,
    *,
    workflow_case: FormWorkflowCase,
    updated_by: int | None,
    status_source: str = "manual",
    note: str | None = None,
) -> FormWorkflowCase:
    """Oznacza zasoby sprawy jako zwolnione bez usuwania historii urządzeń."""
    now = datetime.now(UTC)
    workflow_case.resources_released_at = now
    workflow_case.resources_release_due_at = None
    workflow_case.updated_by = updated_by
    workflow_case.updated_at = now
    append_workflow_status_history(
        workflow_case,
        status_value=normalize_workflow_business_status(workflow_case.business_status),
        status_source=status_source,
        changed_at=now,
        note=note or "Zwolniono zasoby i proformę dla sprawy.",
        metadata={"resources_released": True},
    )
    await session.flush()
    return workflow_case


async def set_form_workflow_delivery(
    session: AsyncSession,
    *,
    workflow_case: FormWorkflowCase,
    delivery_date: date,
    delivery_time_window: str | None,
    delivery_contact_name: str | None,
    delivery_contact_phone: str | None,
    delivery_notes: str | None,
    updated_by: int | None,
) -> FormWorkflowCase:
    """Zapisuje ustalenia logistyczne dowozu po stronie CTIP."""
    workflow_case.delivery_date = delivery_date
    workflow_case.delivery_time_window = _normalize_delivery_text(
        delivery_time_window, max_length=64
    )
    workflow_case.delivery_contact_name = _normalize_delivery_text(
        delivery_contact_name, max_length=160
    )
    workflow_case.delivery_contact_phone = _normalize_delivery_text(
        delivery_contact_phone, max_length=64
    )
    workflow_case.delivery_notes = _normalize_delivery_text(delivery_notes, max_length=2000)
    workflow_case.updated_by = updated_by
    workflow_case.updated_at = datetime.now(UTC)
    await session.flush()
    return workflow_case


async def clear_form_workflow_delivery(
    session: AsyncSession,
    *,
    workflow_case: FormWorkflowCase,
    updated_by: int | None,
) -> FormWorkflowCase:
    """Usuwa ustalenia logistyczne dowozu po stronie CTIP."""
    workflow_case.delivery_date = None
    workflow_case.delivery_time_window = None
    workflow_case.delivery_contact_name = None
    workflow_case.delivery_contact_phone = None
    workflow_case.delivery_notes = None
    workflow_case.updated_by = updated_by
    workflow_case.updated_at = datetime.now(UTC)
    await session.flush()
    return workflow_case


async def map_form_workflow_summaries(
    session: AsyncSession, *, form_request_ids: Iterable[int]
) -> dict[int, dict[str, Any]]:
    """Buduje mapę krótkich podsumowań spraw workflow dla listy formularzy."""
    ids = [int(item) for item in form_request_ids if item]
    if not ids:
        return {}

    cases = list(
        (
            await session.execute(
                select(FormWorkflowCase).where(FormWorkflowCase.form_request_id.in_(ids))
            )
        )
        .scalars()
        .all()
    )
    if not cases:
        return {}

    case_ids = [case.id for case in cases]
    counts_stmt = (
        select(FormWorkflowDevice.workflow_case_id, func.count(FormWorkflowDevice.id))
        .where(FormWorkflowDevice.workflow_case_id.in_(case_ids))
        .group_by(FormWorkflowDevice.workflow_case_id)
    )
    counts = {
        int(case_id): int(device_count)
        for case_id, device_count in (await session.execute(counts_stmt)).all()
    }

    output: dict[int, dict[str, Any]] = {}
    for workflow_case in cases:
        devices_count = counts.get(workflow_case.id, 0)
        output[int(workflow_case.form_request_id)] = {
            "exists": True,
            "id": workflow_case.id,
            "stage": derive_workflow_stage(
                firebird_client_id=workflow_case.firebird_client_id,
                devices_count=devices_count,
                proforma_number=workflow_case.proforma_number,
                proforma_firebird_id=workflow_case.proforma_firebird_id,
            ),
            "business_status": normalize_workflow_business_status(workflow_case.business_status),
            "business_status_raw": workflow_case.business_status,
            "business_status_canonical": normalize_workflow_business_status(
                workflow_case.business_status
            ),
            "business_status_label": workflow_business_status_label(workflow_case.business_status),
            "signature_deadline_at": (
                workflow_case.signature_deadline_at.isoformat()
                if workflow_case.signature_deadline_at
                else None
            ),
            "resources_release_due_at": (
                workflow_case.resources_release_due_at.isoformat()
                if workflow_case.resources_release_due_at
                else None
            ),
            "resources_released_at": (
                workflow_case.resources_released_at.isoformat()
                if workflow_case.resources_released_at
                else None
            ),
            "status_changed_at": (
                workflow_case.status_changed_at.isoformat()
                if workflow_case.status_changed_at
                else None
            ),
            "status_source": workflow_case.status_source,
            "firebird_client_id": workflow_case.firebird_client_id,
            "firebird_client_status": workflow_case.firebird_client_status,
            "client_mode": workflow_case.client_mode,
            "devices_selected_count": devices_count,
            "proforma_firebird_id": workflow_case.proforma_firebird_id,
            "proforma_number": workflow_case.proforma_number,
            "proforma_pdf_path": workflow_case.proforma_pdf_path,
            "proforma_preview_url": _resolve_proforma_preview_url(workflow_case),
            "delivery_planned": workflow_case.delivery_date is not None,
            "delivery_date": (
                workflow_case.delivery_date.isoformat() if workflow_case.delivery_date else None
            ),
            "delivery_label": _build_delivery_label(
                workflow_case.delivery_date,
                workflow_case.delivery_time_window,
            ),
        }
    return output


def _coerce_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _payload_text(payload: dict[str, Any], key: str) -> str:
    raw = payload.get(key)
    if raw is None:
        return ""
    return str(raw).strip()


def _build_address(payload: dict[str, Any], prefix: str) -> str:
    street = _payload_text(payload, f"{prefix}_street")
    building_no = _payload_text(payload, f"{prefix}_building_no")
    apartment_no = _payload_text(payload, f"{prefix}_apartment_no")
    postal_code = _payload_text(payload, f"{prefix}_postal_code")
    city = _payload_text(payload, f"{prefix}_city")
    address_parts = []
    if street:
        suffix = building_no
        if apartment_no:
            suffix = f"{suffix}/{apartment_no}" if suffix else apartment_no
        address_parts.append(f"{street} {suffix}".strip())
    line_two = " ".join(part for part in [postal_code, city] if part).strip()
    if line_two:
        address_parts.append(line_two)
    return ", ".join(part for part in address_parts if part)


def _representative_full_name(representative: dict[str, Any]) -> str:
    return " ".join(
        part.strip()
        for part in [
            str(representative.get("first_name") or ""),
            str(representative.get("last_name") or ""),
        ]
        if part and part.strip()
    ).strip()


def _representative_summary(representative: dict[str, Any]) -> str:
    parts = []
    full_name = _representative_full_name(representative)
    if full_name:
        parts.append(full_name)
    pesel = str(representative.get("pesel") or "").strip()
    if pesel:
        parts.append(f"PESEL {pesel}")
    document_type = str(representative.get("document_type") or "").strip()
    document_number = str(representative.get("document_number") or "").strip()
    if document_type or document_number:
        parts.append(" ".join(part for part in [document_type, document_number] if part).strip())
    return " | ".join(part for part in parts if part)


def _normalize_price_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _normalize_delivery_text(value: Any, *, max_length: int) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return text[:max_length]


def _build_delivery_label(
    delivery_date: date | None, delivery_time_window: str | None
) -> str | None:
    if delivery_date is None:
        return None
    base = delivery_date.isoformat()
    time_window = str(delivery_time_window or "").strip()
    if not time_window:
        return base
    return f"{base} ({time_window})"


def _build_sheet_sync_state(devices: list[FormWorkflowDevice]) -> dict[str, Any]:
    statuses: set[str] = set()
    sheet_rows: list[int] = []
    assignee_label = ""
    assignee_id: int | None = None
    proforma_number = ""
    last_sync_at = ""
    last_error = ""

    for device in devices:
        snapshot = device.snapshot if isinstance(device.snapshot, dict) else {}
        status = str(snapshot.get("sheet_sync_status") or "").strip().lower()
        if status:
            statuses.add(status)
        row_number = _coerce_int(snapshot.get("sheet_row"))
        if row_number:
            sheet_rows.append(row_number)
        if not assignee_label:
            assignee_label = str(snapshot.get("sheet_assignee") or "").strip()
        if assignee_id is None:
            assignee_id = _coerce_int(snapshot.get("sheet_assignee_id"))
        if not proforma_number:
            proforma_number = str(snapshot.get("sheet_proforma_number") or "").strip()
        candidate_sync_at = str(snapshot.get("sheet_sync_updated_at") or "").strip()
        if candidate_sync_at and candidate_sync_at > last_sync_at:
            last_sync_at = candidate_sync_at
        if not last_error:
            last_error = str(snapshot.get("sheet_sync_error") or "").strip()

    state = "none"
    if "error" in statuses:
        state = "error"
    elif "released" in statuses:
        state = "released"
    elif "synced" in statuses:
        state = "synced"

    return {
        "state": state,
        "sheet_rows": sorted(set(sheet_rows)),
        "assignee_label": assignee_label or None,
        "assignee_id": assignee_id,
        "proforma_number": proforma_number or None,
        "last_sync_at": last_sync_at or None,
        "last_error": last_error or None,
    }


__all__ = [
    "WORKFLOW_CLIENT_MODE_BASIC_PROFORMA",
    "WORKFLOW_DEVICE_SOURCE_FIREBIRD_SERIAL",
    "WORKFLOW_DEVICE_SOURCE_FIREBIRD_WAREHOUSE",
    "WORKFLOW_DEVICE_SOURCES",
    "WORKFLOW_DEVICE_SOURCE_GOOGLE_SHEET",
    "WORKFLOW_BUSINESS_STATUS_APPROVED",
    "WORKFLOW_BUSINESS_STATUS_APPROVED_ORDER",
    "WORKFLOW_BUSINESS_STATUS_DRAFT",
    "WORKFLOW_BUSINESS_STATUS_PENDING_APPROVAL",
    "WORKFLOW_BUSINESS_STATUS_REJECTED",
    "WORKFLOW_BUSINESS_STATUS_REJECTED_GRENKE",
    "WORKFLOW_BUSINESS_STATUS_WAITING_SIGNATURE",
    "WORKFLOW_BUSINESS_STATUS_ZEROWKA",
    "WORKFLOW_STAGE_CLIENT_READY",
    "WORKFLOW_STAGE_DEVICES_SELECTED",
    "WORKFLOW_STAGE_FORM_SUBMITTED",
    "WORKFLOW_STAGE_PROFORMA_CREATED",
    "build_sales_packet",
    "build_workflow_device_key",
    "build_workflow_proforma_preview_url",
    "build_workflow_business_status_options",
    "build_client_preview",
    "derive_workflow_stage",
    "get_form_workflow_case",
    "get_or_create_form_workflow_case",
    "list_form_workflow_devices",
    "map_form_workflow_summaries",
    "mark_workflow_resources_released",
    "normalize_workflow_business_status",
    "normalize_workflow_device_source_type",
    "replace_form_workflow_devices",
    "serialize_workflow_case",
    "set_form_workflow_delivery",
    "set_form_workflow_business_status",
    "clear_form_workflow_delivery",
    "clear_form_workflow_proforma",
    "set_form_workflow_client",
    "set_form_workflow_proforma",
    "workflow_business_status_label",
]
