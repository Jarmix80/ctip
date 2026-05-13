"""API dashboardu obslugi umow."""

from __future__ import annotations

import asyncio
import subprocess
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_session_context, get_db_session
from app.models import AdminAuditLog, AdminUser, FormRequest, FormWorkflowCase, FormWorkflowDevice
from app.services import section_permissions
from app.services.audit import record_audit
from app.services.contracts_dashboard import (
    create_client_from_submitted_payload,
    find_client_in_firebird,
    find_client_in_firebird_by_id,
    firebird_writes_enabled,
    load_available_devices_from_firebird_warehouse,
    load_contract_forms,
    load_device_from_sheet_row,
    load_firebird_runtime_config,
    normalize_nip,
    synchronize_device_from_sheet_row,
    use_firebird_runtime_config,
)
from app.services.contracts_mailbox_sync_runtime import (
    parse_mailbox_sync_summary,
    run_mailbox_sync_subprocess,
)
from app.services.contracts_proforma import (
    create_proforma_from_workflow,
    delete_proforma_from_firebird,
)
from app.services.contracts_workflow import (
    WORKFLOW_BUSINESS_STATUS_APPROVED_ORDER,
    WORKFLOW_BUSINESS_STATUS_DRAFT,
    WORKFLOW_BUSINESS_STATUS_REJECTED_GRENKE,
    WORKFLOW_BUSINESS_STATUS_WAITING_SIGNATURE,
    WORKFLOW_CLIENT_MODE_BASIC_PROFORMA,
    WORKFLOW_DEVICE_SOURCE_FIREBIRD_WAREHOUSE,
    build_client_preview,
    build_sales_packet,
    build_workflow_business_status_options,
    build_workflow_device_key,
    clear_form_workflow_delivery,
    clear_form_workflow_proforma,
    get_form_workflow_case,
    get_or_create_form_workflow_case,
    list_form_workflow_devices,
    map_form_workflow_summaries,
    mark_workflow_resources_released,
    normalize_workflow_business_status,
    normalize_workflow_device_source_type,
    replace_form_workflow_devices,
    serialize_workflow_case,
    set_form_workflow_business_status,
    set_form_workflow_client,
    set_form_workflow_delivery,
    set_form_workflow_proforma,
    workflow_business_status_label,
)
from app.services.workflow_machine_binding import (
    apply_binding_snapshot,
    bind_devices_to_workflow_client,
    notify_binding_issues_to_admins,
)
from app.services.workflow_sheet_status_cache import (
    load_workflow_sheet_status_cache_lookup,
    refresh_workflow_sheet_status_cache,
)
from app.services.workflow_sheet_sync import (
    clear_workflow_proforma_from_sheet,
    list_workflow_sheet_assignee_options,
    load_workflow_sheet_runtime_config,
    release_workflow_devices_from_sheet,
    resolve_workflow_sheet_assignee,
    sync_workflow_devices_to_sheet,
    use_workflow_sheet_runtime_config,
    workflow_sheet_sync_configured,
)

router = APIRouter(prefix="/admin/contracts", tags=["admin-contracts"])


def _to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC).isoformat()
    return value.astimezone(UTC).isoformat()


def _parse_datetime_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


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
    source_type: (
        Literal[
            "google_sheet",
            "firebird_magazyn_28",
            "firebird_serial",
        ]
        | None
    ) = None
    price_net: str | None = Field(default=None, max_length=32)
    price_gross: str | None = Field(default=None, max_length=32)


class WorkflowDevicesRequest(BaseModel):
    """Żądanie zapisania wyboru urządzeń do sprawy workflow."""

    rows: list[int] = Field(default_factory=list, max_length=50)
    devices: list[WorkflowDeviceSelection] = Field(default_factory=list, max_length=50)
    sheet_assignee_id: int | None = Field(default=None, ge=1)


class WorkflowStatusRequest(BaseModel):
    """Zadanie zmiany statusu biznesowego sprawy workflow."""

    business_status: str = Field(
        pattern=(
            "^(DRAFT|PENDING_APPROVAL|APPROVED|ZEROWKA|REJECTED|"
            "WAITING_SIGNATURE|APPROVED_ORDER|REJECTED_GRENKE)$"
        ),
    )
    signature_deadline_at: datetime | None = None


class WorkflowProformaRequest(BaseModel):
    """Zadanie utworzenia proformy dla klienta lub banku."""

    for_bank: bool = Field(default=True)
    sheet_assignee_id: int | None = Field(default=None, ge=1)


class WorkflowSheetSyncRequest(BaseModel):
    """Zadanie synchronizacji arkusza urządzeń dla sprawy workflow."""

    sheet_assignee_id: int | None = Field(default=None, ge=1)


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


class WorkflowArchiveRequest(BaseModel):
    """Żądanie ręcznego przeniesienia formularza do archiwum."""

    bucket: Literal["accepted", "rejected", "unfilled"] | None = None


class WorkflowMailboxSyncRequest(BaseModel):
    """Żądanie uruchomienia synchronizacji mailbox -> workflow."""

    limit: int = Field(default=30, ge=1, le=500)
    folder: str = Field(default="INBOX", min_length=1, max_length=128)
    reprocess: bool = False
    dry_run: bool = False
    timeout_seconds: int = Field(default=300, ge=30, le=1800)


WORKFLOW_DEFAULT_VAT = Decimal("23")
PRICE_PRECISION = Decimal("0.01")
WORKFLOW_BANK_CLIENT_ID = 855
WORKFLOW_BANK_CLIENT_NIP = normalize_nip("782-22-75-815")
WORKFLOW_BANK_CLIENT_NAME = "GRENKELEASING Sp. z o.o."
ARCHIVE_BUCKET_ACCEPTED = "accepted"
ARCHIVE_BUCKET_REJECTED = "rejected"
ARCHIVE_BUCKET_UNFILLED = "unfilled"
ARCHIVE_SCOPE_ACTIVE = "active"
ARCHIVE_DAYS_AFTER_DECISION = 14
RESOURCE_RELEASE_DAYS_AFTER_REJECTION = 7


def _tail_text(value: str, *, max_lines: int = 120, max_chars: int = 12000) -> str:
    lines = value.splitlines()
    tail = "\n".join(lines[-max_lines:])
    if len(tail) > max_chars:
        return tail[-max_chars:]
    return tail


def _days_until(value: datetime | None) -> int | None:
    if value is None:
        return None
    target = value if value.tzinfo else value.replace(tzinfo=UTC)
    delta = target.astimezone(UTC) - datetime.now(UTC)
    return max(0, int(delta.total_seconds() // 86400) + (1 if delta.total_seconds() % 86400 else 0))


async def _load_last_mailbox_sync_summary(session: AsyncSession) -> dict[str, Any]:
    stmt = (
        select(AdminAuditLog)
        .where(
            AdminAuditLog.action.in_(
                (
                    "contracts_mailbox_sync_scheduler",
                    "contracts_mailbox_sync_trigger",
                )
            )
        )
        .order_by(AdminAuditLog.created_at.desc(), AdminAuditLog.id.desc())
        .limit(1)
    )
    row = (await session.execute(stmt)).scalars().first()
    if row is None:
        return {
            "available": False,
            "source": None,
            "result": None,
            "last_run_at": None,
            "started_at": None,
            "finished_at": None,
            "exit_code": None,
            "summary": None,
        }

    payload = row.payload if isinstance(row.payload, dict) else {}
    source = "scheduler" if row.action == "contracts_mailbox_sync_scheduler" else "manual"
    exit_code = _coerce_int(payload.get("exit_code"))
    raw_result = str(payload.get("result") or "").strip().lower()
    result = raw_result
    if not result:
        if exit_code is None:
            result = "unknown"
        else:
            result = "ok" if exit_code == 0 else "error"

    started_at = _parse_datetime_iso(payload.get("started_at"))
    finished_at = _parse_datetime_iso(payload.get("finished_at"))
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else None
    last_run_at = finished_at or started_at or row.created_at
    return {
        "available": True,
        "source": source,
        "result": result,
        "last_run_at": _to_iso(last_run_at),
        "started_at": _to_iso(started_at),
        "finished_at": _to_iso(finished_at),
        "exit_code": exit_code,
        "summary": summary,
    }


def _archive_bucket_for_form(
    form: FormRequest,
    workflow_summary: dict[str, Any] | None,
) -> str | None:
    if form.status != "SUBMITTED":
        expires_at = form.token_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return (
            ARCHIVE_BUCKET_UNFILLED
            if form.status == "EXPIRED" or expires_at <= datetime.now(UTC)
            else None
        )
    status_value = normalize_workflow_business_status(
        (workflow_summary or {}).get("business_status")
    )
    if status_value == WORKFLOW_BUSINESS_STATUS_APPROVED_ORDER:
        return ARCHIVE_BUCKET_ACCEPTED
    if status_value == WORKFLOW_BUSINESS_STATUS_REJECTED_GRENKE:
        return ARCHIVE_BUCKET_REJECTED
    return None


def _flow_status_for_form(form: FormRequest, workflow_summary: dict[str, Any] | None) -> dict:
    workflow = workflow_summary or {}
    status_value = normalize_workflow_business_status(workflow.get("business_status"))
    if form.status in {"GENERATED", "DISPATCHED"}:
        return {"value": "FORM_SENT", "label": "Wysłany formularz do klienta"}
    if form.status == "EXPIRED":
        return {"value": "UNFILLED", "label": "Formularz niewypełniony"}
    if status_value == WORKFLOW_BUSINESS_STATUS_WAITING_SIGNATURE:
        return {
            "value": WORKFLOW_BUSINESS_STATUS_WAITING_SIGNATURE,
            "label": workflow_business_status_label(status_value),
        }
    if status_value == WORKFLOW_BUSINESS_STATUS_APPROVED_ORDER:
        return {
            "value": WORKFLOW_BUSINESS_STATUS_APPROVED_ORDER,
            "label": workflow_business_status_label(status_value),
        }
    if status_value == WORKFLOW_BUSINESS_STATUS_REJECTED_GRENKE:
        return {
            "value": WORKFLOW_BUSINESS_STATUS_REJECTED_GRENKE,
            "label": workflow_business_status_label(status_value),
        }
    return {"value": "FORM_SUBMITTED", "label": "Wypełniony formularz klienta"}


def _row_tone_for_form(form: FormRequest, workflow_summary: dict[str, Any] | None) -> str:
    if form.archive_bucket:
        return "muted"
    status_value = normalize_workflow_business_status(
        (workflow_summary or {}).get("business_status")
    )
    if status_value == WORKFLOW_BUSINESS_STATUS_APPROVED_ORDER:
        return "accepted"
    if status_value == WORKFLOW_BUSINESS_STATUS_REJECTED_GRENKE:
        return "muted" if (workflow_summary or {}).get("resources_released_at") else "rejected"
    if form.status == "EXPIRED":
        return "muted"
    return "active"


def _apply_archive_due(form: FormRequest, workflow_summary: dict[str, Any] | None) -> None:
    if form.archive_due_at is not None or form.archive_bucket is not None:
        return
    bucket = _archive_bucket_for_form(form, workflow_summary)
    if bucket in {ARCHIVE_BUCKET_ACCEPTED, ARCHIVE_BUCKET_REJECTED}:
        form.archive_due_at = datetime.now(UTC) + timedelta(days=ARCHIVE_DAYS_AFTER_DECISION)
    elif bucket == ARCHIVE_BUCKET_UNFILLED:
        expires_at = form.token_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= datetime.now(UTC):
            form.archive_due_at = expires_at + timedelta(days=ARCHIVE_DAYS_AFTER_DECISION)


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


def _workflow_device_sort_key(item: dict[str, Any]) -> tuple[str, int]:
    source_type = normalize_workflow_device_source_type(item.get("source_type"))
    try:
        row_number = int(item.get("row") or 0)
    except (TypeError, ValueError):
        row_number = 0
    return (source_type, row_number)


def _build_selected_device_payloads(payload: WorkflowDevicesRequest) -> list[dict[str, str | int]]:
    if payload.devices:
        selected_rows: dict[str, dict[str, str | int]] = {}
        for item in payload.devices:
            source_type = normalize_workflow_device_source_type(
                item.source_type,
                default=WORKFLOW_DEVICE_SOURCE_FIREBIRD_WAREHOUSE,
            )
            source_key = build_workflow_device_key(source_type, item.row)
            if source_key is None:
                continue
            selected_rows[source_key] = {
                "row": int(item.row),
                "source_type": source_type,
                "price_net": _normalize_price_text(item.price_net),
                "price_gross": _normalize_price_text(item.price_gross),
            }
        return sorted(selected_rows.values(), key=_workflow_device_sort_key)

    selected_rows = sorted({int(row) for row in payload.rows if int(row) > 0})
    return [
        {
            "row": row,
            "source_type": WORKFLOW_DEVICE_SOURCE_FIREBIRD_WAREHOUSE,
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


def _build_saved_workflow_device_payload(device) -> dict[str, str | int | bool]:
    snapshot = device.snapshot if isinstance(device.snapshot, dict) else {}
    row_value = device.source_row if device.source_row is not None else snapshot.get("row") or 0
    try:
        row_number = int(row_value)
    except (TypeError, ValueError):
        row_number = 0

    stored_gross_price = _normalize_price_text(device.price_gross) or _normalize_price_text(
        snapshot.get("price_gross")
    )
    stored_price = (
        stored_gross_price
        or _normalize_price_text(device.price)
        or _normalize_price_text(snapshot.get("price"))
    )
    stored_vat_rate = str(snapshot.get("vat_rate") or "23").strip() or "23"
    computed_net_price = _normalize_price_text(device.price_net)
    if not computed_net_price and stored_price:
        parsed_stored_price = _parse_price(stored_price)
        if parsed_stored_price is not None:
            computed_net_price = _format_price(_gross_to_net(parsed_stored_price))

    source_type = normalize_workflow_device_source_type(
        snapshot.get("source_type") or device.source_type,
        default=WORKFLOW_DEVICE_SOURCE_FIREBIRD_WAREHOUSE,
    )
    source_key = build_workflow_device_key(source_type, row_number) or ""
    warehouse_id = snapshot.get("ms_id_magazyn_table") or (
        row_number
        if source_type == WORKFLOW_DEVICE_SOURCE_FIREBIRD_WAREHOUSE and row_number > 0
        else ""
    )

    return {
        "row": row_number,
        "source_key": source_key,
        "producer": str(snapshot.get("producer") or device.producer or "").strip(),
        "model": str(snapshot.get("model") or device.model or "").strip(),
        "device_label": " ".join(
            part.strip()
            for part in [
                str(snapshot.get("producer") or device.producer or "").strip(),
                str(snapshot.get("model") or device.model or "").strip(),
            ]
            if part and part.strip()
        ).strip(),
        "serial": str(snapshot.get("serial") or device.serial or "").strip(),
        "ewidencja": str(snapshot.get("ewidencja") or device.ewidencja or "").strip(),
        "index": str(
            snapshot.get("index") or snapshot.get("ewidencja") or device.ewidencja or ""
        ).strip(),
        "name": str(
            snapshot.get("name") or snapshot.get("description") or device.model or ""
        ).strip(),
        "status": str(snapshot.get("status") or device.device_status or "").strip(),
        "price": stored_price,
        "price_net": computed_net_price,
        "price_gross": stored_gross_price or stored_price,
        "vat_rate": stored_vat_rate,
        "reservation": str(snapshot.get("reservation") or "").strip(),
        "reservation_status": str(
            snapshot.get("reservation_status") or device.reservation_status or ""
        ).strip(),
        "reservation_filter_value": (
            "Zarezerwowana"
            if str(snapshot.get("reservation_status") or device.reservation_status or "").strip()
            else "Brak rezerwacji"
        ),
        "reservation_form_id": _coerce_int(snapshot.get("ctip_form_id")),
        "reservation_case_id": _coerce_int(snapshot.get("ctip_workflow_case_id")),
        "reservation_initials": _build_person_initials(snapshot.get("sheet_assignee")),
        "reservation_badge_class": (
            "danger"
            if str(snapshot.get("reservation_status") or device.reservation_status or "").strip()
            else "soft"
        ),
        "locked_by_other": False,
        "locked_reason": "",
        "description": str(snapshot.get("description") or "").strip(),
        "available_quantity": str(snapshot.get("available_quantity") or "").strip(),
        "reserved_quantity": str(snapshot.get("reserved_quantity") or "").strip(),
        "warehouse_quantity": str(snapshot.get("warehouse_quantity") or "").strip(),
        "serial_required": str(snapshot.get("serial_required") or "").strip(),
        "ms_id_maszyna": str(
            device.firebird_machine_id or snapshot.get("ms_id_maszyna") or ""
        ).strip(),
        "ms_id_klient": str(
            device.firebird_client_id or snapshot.get("ms_id_klient") or ""
        ).strip(),
        "ms_id_model": str(snapshot.get("ms_id_model") or "").strip(),
        "ms_id_magazyn_table": str(warehouse_id).strip(),
        "sheet_row": _coerce_int(snapshot.get("sheet_row")),
        "source_type": source_type,
        "selected": True,
    }


def _build_available_workflow_devices(
    source_devices: list[dict[str, str]],
    *,
    saved_devices_by_key: dict[str, Any],
    active_reservations_by_key: dict[str, dict[str, Any]] | None = None,
    workflow_sheet_lookup: dict[str, Any] | None = None,
) -> list[dict[str, str | int | bool]]:
    available_devices: list[dict[str, str | int | bool]] = []
    seen_keys: set[str] = set()
    reservations = active_reservations_by_key or {}
    sheet_lookup = workflow_sheet_lookup or {}

    for source_device in source_devices:
        source_type = normalize_workflow_device_source_type(
            source_device.get("source_type"),
            default=WORKFLOW_DEVICE_SOURCE_FIREBIRD_WAREHOUSE,
        )
        try:
            row_number = int(source_device.get("row") or 0)
        except (TypeError, ValueError):
            row_number = 0
        if row_number <= 0:
            continue

        source_key = build_workflow_device_key(source_type, row_number)
        if source_key is None:
            continue
        saved_device = saved_devices_by_key.get(source_key)
        saved_snapshot = (
            saved_device.snapshot
            if saved_device and isinstance(saved_device.snapshot, dict)
            else {}
        )
        stored_price = _normalize_price_text(
            source_device.get("price_gross") or source_device.get("price")
        )
        stored_net_price = _normalize_price_text(source_device.get("price_net"))
        if saved_device is not None:
            stored_net_price = _normalize_price_text(saved_device.price_net) or stored_net_price
            stored_price = _normalize_price_text(saved_device.price_gross) or stored_price

        if not stored_net_price and stored_price:
            parsed_gross_price = _parse_price(stored_price)
            if parsed_gross_price is not None:
                stored_net_price = _format_price(_gross_to_net(parsed_gross_price))

        reservation_entry = reservations.get(source_key)
        sheet_entry = _resolve_workflow_sheet_entry(
            sheet_lookup,
            source_key=source_key,
            index_value=source_device.get("index") or source_device.get("ewidencja"),
        )
        status_value = str(sheet_entry.get("status") or source_device.get("status") or "").strip()
        reservation_form_id = _coerce_int(
            sheet_entry.get("ctip_form_id")
            or saved_snapshot.get("ctip_form_id")
            or (reservation_entry or {}).get("form_request_id")
        )
        reservation_case_id = _coerce_int(
            sheet_entry.get("ctip_workflow_case_id")
            or saved_snapshot.get("ctip_workflow_case_id")
            or (reservation_entry or {}).get("workflow_case_id")
        )
        reservation_person = str(
            (reservation_entry or {}).get("reserved_by_label")
            or sheet_entry.get("reservation_grenke")
            or saved_snapshot.get("sheet_assignee")
            or source_device.get("reservation")
            or ""
        ).strip()
        reservation_initials = str(
            (reservation_entry or {}).get("reserved_by_initials")
            or _build_person_initials(reservation_person)
            or ""
        ).strip()
        reservation_status = "Brak rezerwacji"
        reservation_filter_value = "Brak rezerwacji"
        reservation_badge_class = "soft"
        if reservation_initials or reservation_form_id:
            reservation_status = (
                f"Zarezerwowana przez {reservation_initials}"
                if reservation_initials
                else "Zarezerwowana"
            )
            reservation_filter_value = "Zarezerwowana"
            reservation_badge_class = "danger"
        locked_by_other = reservation_entry is not None and saved_device is None

        next_item = {
            "row": row_number,
            "source_key": source_key,
            "producer": source_device.get("producer") or "",
            "model": source_device.get("model") or "",
            "device_label": " ".join(
                part.strip()
                for part in [
                    str(source_device.get("producer") or "").strip(),
                    str(source_device.get("model") or "").strip(),
                ]
                if part and str(part).strip()
            ).strip(),
            "serial": source_device.get("serial") or "",
            "ewidencja": source_device.get("ewidencja") or "",
            "index": source_device.get("index") or source_device.get("ewidencja") or "",
            "name": source_device.get("name") or source_device.get("description") or "",
            "status": status_value,
            "price": source_device.get("price") or "",
            "price_net": stored_net_price,
            "price_gross": stored_price,
            "vat_rate": source_device.get("vat_rate") or "23",
            "reservation": reservation_person,
            "reservation_status": reservation_status,
            "reservation_filter_value": reservation_filter_value,
            "reservation_form_id": reservation_form_id,
            "reservation_case_id": reservation_case_id,
            "reservation_initials": reservation_initials,
            "reservation_badge_class": reservation_badge_class,
            "locked_by_other": locked_by_other,
            "locked_reason": (
                f"Urzadzenie jest zapisane w formularzu {reservation_form_id}."
                if locked_by_other and reservation_form_id
                else (
                    "Urzadzenie jest zapisane w innej aktywnej sprawie workflow."
                    if locked_by_other
                    else ""
                )
            ),
            "description": source_device.get("description") or "",
            "available_quantity": source_device.get("available_quantity") or "",
            "reserved_quantity": source_device.get("reserved_quantity") or "",
            "warehouse_quantity": source_device.get("warehouse_quantity") or "",
            "serial_required": source_device.get("serial_required") or "",
            "ms_id_maszyna": source_device.get("ms_id_maszyna") or "",
            "ms_id_klient": source_device.get("ms_id_klient") or "",
            "ms_id_model": source_device.get("ms_id_model") or "",
            "ms_id_magazyn_table": source_device.get("ms_id_magazyn_table")
            or (
                str(row_number) if source_type == WORKFLOW_DEVICE_SOURCE_FIREBIRD_WAREHOUSE else ""
            ),
            "sheet_row": _coerce_int(sheet_entry.get("sheet_row")),
            "source_type": source_type,
            "selected": saved_device is not None,
        }
        available_devices.append(next_item)
        seen_keys.add(source_key)

    missing_saved_keys = sorted(set(saved_devices_by_key) - seen_keys)
    for source_key in missing_saved_keys:
        available_devices.append(
            _build_saved_workflow_device_payload(saved_devices_by_key[source_key])
        )

    return sorted(available_devices, key=_workflow_device_sort_key)


def _coerce_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clean_person_label(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text.split("(", 1)[0].strip() or text


def _build_person_initials(value: str | None) -> str:
    tokens = [token[:1].upper() for token in _clean_person_label(value).split() if token.strip()]
    return "".join(tokens[:3])


def _build_workflow_reservation_person(
    case: FormWorkflowCase,
    device: FormWorkflowDevice,
    updated_by_user: AdminUser | None,
) -> str:
    snapshot = device.snapshot if isinstance(device.snapshot, dict) else {}
    from_snapshot = _clean_person_label(snapshot.get("sheet_assignee"))
    if from_snapshot:
        return from_snapshot
    if updated_by_user is not None:
        full_name = " ".join(
            part.strip()
            for part in [
                updated_by_user.first_name or "",
                updated_by_user.last_name or "",
            ]
            if part and part.strip()
        ).strip()
        if full_name:
            return full_name
        return str(updated_by_user.email or "").strip()
    return ""


async def _load_active_workflow_device_reservations(
    session: AsyncSession,
    *,
    exclude_workflow_case_id: int | None = None,
) -> dict[str, dict[str, Any]]:
    stmt = (
        select(FormWorkflowDevice, FormWorkflowCase, FormRequest, AdminUser)
        .join(FormWorkflowCase, FormWorkflowCase.id == FormWorkflowDevice.workflow_case_id)
        .join(FormRequest, FormRequest.id == FormWorkflowCase.form_request_id)
        .outerjoin(AdminUser, AdminUser.id == FormWorkflowCase.updated_by)
        .where(
            FormRequest.status == "SUBMITTED",
            FormWorkflowCase.resources_released_at.is_(None),
        )
    )
    if exclude_workflow_case_id:
        stmt = stmt.where(FormWorkflowCase.id != exclude_workflow_case_id)

    rows = (await session.execute(stmt)).all()
    reservations: dict[str, dict[str, Any]] = {}
    for device, workflow_case, form_request, updated_by_user in rows:
        source_key = build_workflow_device_key(device.source_type, device.source_row)
        if source_key is None:
            continue
        person_label = _build_workflow_reservation_person(workflow_case, device, updated_by_user)
        initials = _build_person_initials(person_label) or "CTIP"
        reservation_status = f"Zarezerwowana przez {initials}"
        candidate = {
            "source_key": source_key,
            "workflow_case_id": workflow_case.id,
            "form_request_id": form_request.id,
            "business_status": workflow_case.business_status,
            "business_status_label": workflow_business_status_label(workflow_case.business_status),
            "reserved_by_label": person_label,
            "reserved_by_initials": initials,
            "reservation_status": reservation_status,
            "updated_at": workflow_case.updated_at,
            "updated_by_user_id": updated_by_user.id if updated_by_user is not None else None,
        }
        existing = reservations.get(source_key)
        if existing is None or candidate["updated_at"] >= existing["updated_at"]:
            reservations[source_key] = candidate

    return reservations


def _resolve_workflow_sheet_entry(
    sheet_lookup: dict[str, Any],
    *,
    source_key: str | None,
    index_value: str | None,
) -> dict[str, str]:
    by_source_key = sheet_lookup.get("by_source_key") if isinstance(sheet_lookup, dict) else {}
    by_index = sheet_lookup.get("by_index") if isinstance(sheet_lookup, dict) else {}
    if source_key and isinstance(by_source_key, dict):
        match = by_source_key.get(source_key)
        if isinstance(match, dict):
            return match
    normalized_index = str(index_value or "").strip().upper()
    normalized_index = "".join(ch for ch in normalized_index if ch.isalnum())
    if normalized_index and isinstance(by_index, dict):
        match = by_index.get(normalized_index)
        if isinstance(match, dict):
            return match
    return {}


def _workflow_case_company_name(workflow_case: FormWorkflowCase | None) -> str:
    payload = (
        workflow_case.client_payload_snapshot
        if workflow_case is not None and isinstance(workflow_case.client_payload_snapshot, dict)
        else {}
    )
    return str(payload.get("company_name") or "").strip()


def _build_sheet_device_payload(device) -> dict[str, Any]:
    snapshot = device.snapshot if isinstance(device.snapshot, dict) else {}
    row_value = device.source_row if device.source_row is not None else snapshot.get("row")
    source_type = normalize_workflow_device_source_type(
        snapshot.get("source_type") or device.source_type,
        default=WORKFLOW_DEVICE_SOURCE_FIREBIRD_WAREHOUSE,
    )
    return {
        "source_row": _coerce_int(row_value),
        "row": _coerce_int(row_value),
        "source_type": source_type,
        "source_key": build_workflow_device_key(source_type, row_value),
        "sheet_row": _coerce_int(snapshot.get("sheet_row")),
        "sheet_previous_status": str(snapshot.get("sheet_previous_status") or "").strip(),
        "producer": str(snapshot.get("producer") or device.producer or "").strip(),
        "model": str(snapshot.get("model") or device.model or "").strip(),
        "serial": str(snapshot.get("serial") or device.serial or "").strip(),
        "ewidencja": str(snapshot.get("ewidencja") or device.ewidencja or "").strip(),
        "index": str(
            snapshot.get("index") or snapshot.get("ewidencja") or device.ewidencja or ""
        ).strip(),
        "name": str(
            snapshot.get("name") or snapshot.get("description") or device.model or ""
        ).strip(),
        "ms_id_maszyna": _coerce_int(snapshot.get("ms_id_maszyna") or device.firebird_machine_id),
    }


def _build_sheet_release_payloads(
    workflow_devices: list[Any],
    *,
    include_all: bool = False,
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for device in workflow_devices:
        payload = _build_sheet_device_payload(device)
        snapshot = device.snapshot if isinstance(device.snapshot, dict) else {}
        status = str(snapshot.get("sheet_sync_status") or "").strip().lower()
        if include_all or payload.get("sheet_row") or status in {"synced", "error", "released"}:
            payloads.append(payload)
    return payloads


def _apply_sheet_sync_snapshot(
    workflow_devices: list[Any],
    *,
    operation: str,
    sheet_result: dict[str, Any] | None,
    assignee_id: int | None,
    assignee_label: str | None,
    proforma_number: str | None,
    error: str | None = None,
) -> None:
    rows_by_source: dict[int, dict[str, Any]] = {}
    if isinstance(sheet_result, dict):
        for item in sheet_result.get("rows", []):
            if not isinstance(item, dict):
                continue
            source_row = _coerce_int(item.get("source_row"))
            if source_row is None:
                continue
            rows_by_source[source_row] = item

    updated_at = datetime.now(UTC).isoformat()
    for device in workflow_devices:
        snapshot = dict(device.snapshot or {})
        source_row = _coerce_int(
            device.source_row if device.source_row is not None else snapshot.get("row")
        )
        row_entry = rows_by_source.get(source_row) if source_row is not None else None
        if row_entry and _coerce_int(row_entry.get("sheet_row")):
            snapshot["sheet_row"] = _coerce_int(row_entry.get("sheet_row"))
        previous_status = str(row_entry.get("previous_status") or "").strip() if row_entry else ""
        if previous_status:
            snapshot["sheet_previous_status"] = previous_status

        if assignee_id is not None:
            snapshot["sheet_assignee_id"] = assignee_id
        if assignee_label:
            snapshot["sheet_assignee"] = assignee_label
        if proforma_number:
            snapshot["sheet_proforma_number"] = proforma_number
        elif operation == "proforma_cleared":
            snapshot["sheet_proforma_number"] = ""

        snapshot["sheet_sync_updated_at"] = updated_at
        if error:
            snapshot["sheet_sync_status"] = "error"
            snapshot["sheet_sync_error"] = error
        else:
            snapshot["sheet_sync_status"] = operation
            snapshot["sheet_sync_error"] = None
            if operation == "released":
                snapshot["sheet_release_at"] = updated_at
        device.snapshot = snapshot


async def _resolve_sheet_assignee_selection(
    *,
    session: AsyncSession,
    admin_user,
    explicit_assignee_id: int | None,
    fallback_label: str,
) -> tuple[int | None, str]:
    if explicit_assignee_id is not None:
        option = await resolve_workflow_sheet_assignee(session, explicit_assignee_id)
        return (
            _coerce_int(option.get("id")),
            str(option.get("label") or option.get("login_user") or "").strip(),
        )

    mapped_id = _coerce_int(getattr(admin_user, "firebird_app_user_id", None))
    if mapped_id is not None:
        try:
            option = await resolve_workflow_sheet_assignee(session, mapped_id)
            return (
                _coerce_int(option.get("id")),
                str(option.get("label") or option.get("login_user") or "").strip(),
            )
        except (RuntimeError, ValueError):
            pass

    mapped_login = str(getattr(admin_user, "firebird_app_user_login", "") or "").strip()
    if mapped_login:
        return mapped_id, mapped_login
    return None, fallback_label


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
    archive_scope: str = Query(
        default=ARCHIVE_SCOPE_ACTIVE,
        pattern="^(active|accepted|rejected|unfilled)$",
    ),
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
    mailbox_sync_summary = await _load_last_mailbox_sync_summary(session)

    submitted_only = forms_scope != "all"
    forms = await load_contract_forms(session, limit=500, submitted_only=submitted_only)
    workflow_summaries = await map_form_workflow_summaries(
        session, form_request_ids=[item.id for item in forms]
    )
    for form in forms:
        _apply_archive_due(form, workflow_summaries.get(form.id))
    await session.commit()
    firebird_config = await load_firebird_runtime_config(session)

    from app.services import form_generator

    form_status_totals = {
        "GENERATED": 0,
        "DISPATCHED": 0,
        "SUBMITTED": 0,
        "EXPIRED": 0,
    }
    form_items: list[dict] = []
    firebird_client_cache: dict[str, object] = {}

    with use_firebird_runtime_config(firebird_config):

        async def resolve_client_match(nip: str):
            if not nip:
                return None
            if nip not in firebird_client_cache:
                firebird_client_cache[nip] = await asyncio.to_thread(find_client_in_firebird, nip)
            return firebird_client_cache[nip]

        archive_totals = {
            "active": 0,
            ARCHIVE_BUCKET_ACCEPTED: 0,
            ARCHIVE_BUCKET_REJECTED: 0,
            ARCHIVE_BUCKET_UNFILLED: 0,
        }
        scoped_forms: list[FormRequest] = []
        for item in forms:
            bucket = item.archive_bucket
            if bucket in archive_totals:
                archive_totals[bucket] += 1
            else:
                archive_totals["active"] += 1
            if archive_scope == ARCHIVE_SCOPE_ACTIVE and bucket is None:
                scoped_forms.append(item)
            elif archive_scope != ARCHIVE_SCOPE_ACTIVE and bucket == archive_scope:
                scoped_forms.append(item)

        for item in scoped_forms:
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
                    contract_action = (
                        "podlacz_klienta" if firebird_match.found else "utworz_klienta"
                    )
            else:
                nip = ""

            workflow_summary = workflow_summaries.get(item.id, serialize_workflow_case(None))
            flow_status = _flow_status_for_form(item, workflow_summary)
            archive_bucket = _archive_bucket_for_form(item, workflow_summary)
            available_actions = {
                "workflow": item.status == "SUBMITTED" and item.archive_bucket is None,
                "proforma": item.status == "SUBMITTED" and item.archive_bucket is None,
                "status_change": item.status == "SUBMITTED" and item.archive_bucket is None,
                "summary": flow_status["value"] == WORKFLOW_BUSINESS_STATUS_APPROVED_ORDER,
                "release_resources": (
                    flow_status["value"] == WORKFLOW_BUSINESS_STATUS_REJECTED_GRENKE
                    and not workflow_summary.get("resources_released_at")
                    and item.archive_bucket is None
                ),
                "archive": item.archive_bucket is None and archive_bucket is not None,
                "extend_archive": item.archive_bucket is None and item.archive_due_at is not None,
            }
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
                    "customer_email": str(
                        payload.get("company_email") or item.customer_email or ""
                    ),
                    "customer_phone": str(
                        payload.get("company_phone") or item.customer_phone or ""
                    ),
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
                    "flow_status": flow_status,
                    "archive_state": {
                        "scope": archive_scope,
                        "bucket": item.archive_bucket,
                        "target_bucket": archive_bucket,
                        "archived_at": _to_iso(item.archived_at),
                        "archive_due_at": _to_iso(item.archive_due_at),
                        "days_to_archive": _days_until(item.archive_due_at),
                    },
                    "days_to_resource_release": _days_until(
                        _parse_datetime_iso(workflow_summary.get("resources_release_due_at"))
                    ),
                    "row_tone": _row_tone_for_form(item, workflow_summary),
                    "available_actions": available_actions,
                }
            )

        devices_output: list[dict] = []
        if include_devices:
            try:
                firebird_devices = await asyncio.to_thread(
                    load_available_devices_from_firebird_warehouse
                )
            except Exception as exc:  # noqa: BLE001
                firebird_devices = []
                warnings.append(f"Blad odczytu pozycji magazynowych Firebird: {exc}")
            for device in firebird_devices:
                devices_output.append(
                    {
                        "row": int(device.get("row") or 0),
                        "serial": device.get("serial") or "",
                        "ewidencja": device.get("index") or device.get("ewidencja") or "",
                        "model": device.get("model") or "",
                        "name": device.get("name") or "",
                        "available_quantity": device.get("available_quantity") or "",
                        "reservation_status": device.get("reservation_status") or "",
                        "price_gross": device.get("price_gross") or device.get("price") or "",
                        "found_in_firebird": True,
                        "id_maszyna": None,
                        "id_klient": None,
                        "id_umowacpc": None,
                        "firebird_error": None,
                        "sync_action": "",
                    }
                )
        matched_count = sum(
            1
            for item in devices_output
            if str(item.get("reservation_status") or "").strip().lower() == "brak rezerwacji"
        )
    return {
        "forms_scope": forms_scope,
        "archive_scope": archive_scope,
        "archive_totals": archive_totals,
        "mailbox_sync": mailbox_sync_summary,
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
    firebird_config = await load_firebird_runtime_config(session)
    with use_firebird_runtime_config(firebird_config):
        firebird_match = await asyncio.to_thread(find_client_in_firebird, nip) if nip else None
        source_devices = await asyncio.to_thread(load_available_devices_from_firebird_warehouse)

    workflow_case = await get_form_workflow_case(session, form_request_id=item.id)
    workflow_devices = (
        await list_form_workflow_devices(session, workflow_case_id=workflow_case.id)
        if workflow_case is not None
        else []
    )
    active_reservations = await _load_active_workflow_device_reservations(
        session,
        exclude_workflow_case_id=workflow_case.id if workflow_case is not None else None,
    )
    saved_devices_by_row = {
        device_key: device
        for device in workflow_devices
        if (
            device.source_row is not None
            and (device_key := build_workflow_device_key(device.source_type, device.source_row))
        )
    }
    sheet_config = await load_workflow_sheet_runtime_config(session)
    sheet_lookup = await load_workflow_sheet_status_cache_lookup(session, config=sheet_config)

    available_devices = _build_available_workflow_devices(
        source_devices,
        saved_devices_by_key=saved_devices_by_row,
        active_reservations_by_key=active_reservations,
        workflow_sheet_lookup=sheet_lookup,
    )
    workflow_payload = serialize_workflow_case(workflow_case, workflow_devices)
    with use_workflow_sheet_runtime_config(sheet_config):
        sheet_sync_enabled, sheet_sync_reason = workflow_sheet_sync_configured()
    sheet_assignee_options: list[dict[str, Any]] = []
    sheet_assignee_warning: str | None = None
    try:
        sheet_assignee_options = await list_workflow_sheet_assignee_options(session)
    except RuntimeError as exc:
        sheet_assignee_warning = str(exc)

    selected_assignee_id = _coerce_int(workflow_payload.get("sheet_sync", {}).get("assignee_id"))
    if selected_assignee_id is None:
        selected_assignee_id = _coerce_int(getattr(admin_user, "firebird_app_user_id", None))

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
        "workflow": workflow_payload,
        "workflow_status_action": {
            "current": (
                normalize_workflow_business_status(workflow_case.business_status)
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
        "sheet_sync_config": {
            "enabled": bool(sheet_sync_enabled),
            "reason": sheet_sync_reason,
            "warning": sheet_assignee_warning,
            "source": sheet_config.source,
        },
        "sheet_status_cache": {
            "enabled": bool(sheet_lookup.get("enabled")),
            "reason": sheet_lookup.get("reason"),
            "worksheet_title": sheet_lookup.get("worksheet_title"),
            "last_sync_at": sheet_lookup.get("last_sync_at"),
            "last_error": sheet_lookup.get("last_error"),
            "stale": bool(sheet_lookup.get("stale")),
            "row_count": int(sheet_lookup.get("row_count") or 0),
            "refresh_enabled": bool(sheet_lookup.get("refresh_enabled")),
            "refresh_reason": sheet_lookup.get("refresh_reason"),
            "refresh_interval_seconds": int(sheet_lookup.get("refresh_interval_seconds") or 0),
        },
        "sheet_assignee_options": sheet_assignee_options,
        "sheet_assignee_selected_id": selected_assignee_id,
        "available_devices": available_devices,
        "selection_capabilities": {
            "search": True,
            "status_filter": True,
            "reservation_filter": True,
            "format_filter": False,
            "color_filter": False,
            "note": (
                "Biezace zrodlo urzadzen pochodzi z pozycji magazynowych Firebird dla magazynu 28. "
                "Obecnie sa to wpisy handlowe producent + model + serial zapisane jako osobne pozycje MAGAZYN. "
                "Na etapie handlowca pracujemy na bycie sprzedazowym MAGAZYN, a drugi wariant oparty stricte "
                "o tabele SERIAL ma juz przygotowany tor `source_type`, ale nie jest jeszcze aktywnym "
                "zrodlem listy handlowca. "
                "Ceny w FLOW mozna wpisywac recznie dla kazdego urzadzenia; domyslnie podpowiadane sa "
                "wartosci netto/brutto z Firebird."
            ),
        },
        "sales_packet": build_sales_packet(workflow_case, workflow_devices),
    }


@router.post(
    "/workflow/sheet-status-refresh",
    summary="Odswieza lokalny cache statusow urzadzen z arkusza Google",
)
async def contracts_workflow_sheet_status_refresh(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Odswieza cache statusow arkusza wykorzystywany przez modal wyboru urzadzen."""

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

    result = await refresh_workflow_sheet_status_cache(session, user_id=admin_user.id)
    await record_audit(
        session,
        user_id=admin_user.id,
        action="workflow_sheet_status_cache_refresh",
        client_ip=admin_session.client_ip,
        payload={
            "success": bool(result.get("success")),
            "message": result.get("message"),
            "worksheet_title": result.get("worksheet_title"),
            "row_count": result.get("row_count"),
            "refreshed_count": result.get("refreshed_count"),
            "last_sync_at": result.get("last_sync_at"),
            "last_error": result.get("last_error"),
        },
    )
    await session.commit()
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=result.get("message") or "Nie udalo sie odswiezyc cache statusow arkusza.",
        )
    return result


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
    firebird_config = await load_firebird_runtime_config(session)
    with use_firebird_runtime_config(firebird_config):
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
    previous_workflow_devices = await list_form_workflow_devices(
        session, workflow_case_id=workflow_case.id
    )
    previous_devices_by_key = {
        build_workflow_device_key(device.source_type, device.source_row): device
        for device in previous_workflow_devices
        if build_workflow_device_key(device.source_type, device.source_row)
    }

    selected_payloads = _build_selected_device_payloads(payload)
    selected_rows = [int(item["row"]) for item in selected_payloads]
    selected_devices_meta = [
        {
            "row": int(item["row"]),
            "source_type": normalize_workflow_device_source_type(
                item.get("source_type"),
                default=WORKFLOW_DEVICE_SOURCE_FIREBIRD_WAREHOUSE,
            ),
            "source_key": build_workflow_device_key(item.get("source_type"), item.get("row")),
        }
        for item in selected_payloads
    ]
    firebird_config = await load_firebird_runtime_config(session)
    with use_firebird_runtime_config(firebird_config):
        available_rows = {
            device_key: item
            for item in await asyncio.to_thread(load_available_devices_from_firebird_warehouse)
            if (
                int(item.get("row") or 0) > 0
                and (
                    device_key := build_workflow_device_key(
                        item.get("source_type") or WORKFLOW_DEVICE_SOURCE_FIREBIRD_WAREHOUSE,
                        item.get("row"),
                    )
                )
            )
        }
    missing_rows = [
        item["source_key"] or f"{item['source_type']}:{item['row']}"
        for item in selected_devices_meta
        if item["source_key"] not in available_rows
    ]
    if missing_rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "Nie znaleziono w aktywnym zrodle workflow pozycji: "
                f"{', '.join(map(str, missing_rows))}."
            ),
        )

    active_reservations = await _load_active_workflow_device_reservations(
        session,
        exclude_workflow_case_id=workflow_case.id,
    )
    conflicting_reservations = []
    for selected_item in selected_devices_meta:
        source_key = str(selected_item.get("source_key") or "").strip()
        reservation_entry = active_reservations.get(source_key)
        if reservation_entry is None:
            continue
        conflicting_reservations.append(
            {
                "source_key": source_key,
                "form_request_id": reservation_entry["form_request_id"],
                "workflow_case_id": reservation_entry["workflow_case_id"],
                "reserved_by_initials": reservation_entry["reserved_by_initials"],
            }
        )
    if conflicting_reservations:
        reservation_labels = ", ".join(
            f"{item['source_key']} -> formularz {item['form_request_id']} ({item['reserved_by_initials']})"
            for item in conflicting_reservations
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Wybrane urzadzenia sa juz zapisane w innych aktywnych formularzach workflow: "
                f"{reservation_labels}."
            ),
        )

    selected_devices = []
    for selected_item in selected_payloads:
        source_type = normalize_workflow_device_source_type(
            selected_item.get("source_type"),
            default=WORKFLOW_DEVICE_SOURCE_FIREBIRD_WAREHOUSE,
        )
        source_key = build_workflow_device_key(source_type, selected_item["row"])
        assert source_key is not None
        source_device = dict(available_rows[source_key])
        source_device["price_net"] = selected_item["price_net"]
        source_device["price_gross"] = selected_item["price_gross"]
        source_device["source_type"] = source_type
        source_device["source_key"] = source_key
        selected_devices.append(source_device)
    workflow_devices = await replace_form_workflow_devices(
        session,
        workflow_case=workflow_case,
        selected_devices=selected_devices,
        updated_by=admin_user.id,
    )
    current_devices_by_key = {
        build_workflow_device_key(device.source_type, device.source_row): device
        for device in workflow_devices
        if build_workflow_device_key(device.source_type, device.source_row)
    }
    removed_workflow_devices = [
        device
        for source_key, device in previous_devices_by_key.items()
        if source_key not in current_devices_by_key
    ]

    issuer_name = (
        " ".join(
            part.strip()
            for part in [admin_user.first_name or "", admin_user.last_name or ""]
            if part and part.strip()
        ).strip()
        or admin_user.email
    )
    try:
        sheet_assignee_id, sheet_assignee_label = await _resolve_sheet_assignee_selection(
            session=session,
            admin_user=admin_user,
            explicit_assignee_id=payload.sheet_assignee_id,
            fallback_label=issuer_name,
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

    sheet_sync_result: dict[str, Any] | None = None
    sheet_release_result: dict[str, Any] | None = None
    sheet_sync_warning: str | None = None
    sheet_sync_reason: str | None = None
    sheet_release_warning: str | None = None
    binding_items_payload: list[dict[str, Any]] = []
    binding_alert_payload: dict[str, Any] | None = None
    binding_failures_count = 0

    normalized_status = normalize_workflow_business_status(workflow_case.business_status)
    if normalized_status == WORKFLOW_BUSINESS_STATUS_APPROVED_ORDER and workflow_devices:
        binding_items, _ = await asyncio.to_thread(
            bind_devices_to_workflow_client,
            workflow_case=workflow_case,
            devices=workflow_devices,
            actor_label=issuer_name,
        )
        binding_items_payload = [item.as_dict() for item in binding_items]
        binding_by_device_id = {item.workflow_device_id: item for item in binding_items}
        for workflow_device in workflow_devices:
            apply_binding_snapshot(
                device=workflow_device,
                item=binding_by_device_id.get(workflow_device.id),
            )

        binding_failures = [item for item in binding_items if not item.ok]
        binding_failures_count = len(binding_failures)
        if binding_failures:
            binding_alert_payload = await notify_binding_issues_to_admins(
                session,
                workflow_case=workflow_case,
                form_request_id=item.id,
                failures=binding_failures,
                triggered_by_user_id=admin_user.id,
            )

    current_sheet_payloads = [_build_sheet_device_payload(device) for device in workflow_devices]
    removed_sheet_payloads = _build_sheet_release_payloads(
        removed_workflow_devices, include_all=True
    )
    sheet_config = await load_workflow_sheet_runtime_config(session)
    try:
        with use_workflow_sheet_runtime_config(sheet_config):
            if removed_sheet_payloads:
                sheet_release_result = await asyncio.to_thread(
                    release_workflow_devices_from_sheet,
                    devices=removed_sheet_payloads,
                )
            if current_sheet_payloads:
                sheet_sync_result = await asyncio.to_thread(
                    sync_workflow_devices_to_sheet,
                    devices=current_sheet_payloads,
                    assignee_label=sheet_assignee_label,
                    proforma_number=workflow_case.proforma_number or "",
                    form_request_id=item.id,
                    workflow_case_id=workflow_case.id,
                    business_status_label=workflow_business_status_label(
                        workflow_case.business_status
                    ),
                    reservation_client_name=_workflow_case_company_name(workflow_case),
                )
    except Exception as exc:  # noqa: BLE001
        sheet_sync_warning = str(exc).strip() or (
            f"{type(exc).__name__} podczas synchronizacji arkusza Google."
        )

    if (
        removed_sheet_payloads
        and sheet_release_result
        and sheet_release_result.get("enabled")
        and not sheet_release_warning
    ):
        released_count = int(sheet_release_result.get("released_count") or 0)
        expected_count = len(removed_sheet_payloads)
        if released_count < expected_count:
            sheet_release_warning = (
                "Nie udalo sie zwolnic wszystkich poprzednich rezerwacji arkusza "
                f"({released_count}/{expected_count})."
            )

    if workflow_devices:
        if sheet_sync_result is not None:
            if sheet_sync_result.get("enabled"):
                _apply_sheet_sync_snapshot(
                    workflow_devices,
                    operation="synced",
                    sheet_result=sheet_sync_result,
                    assignee_id=sheet_assignee_id,
                    assignee_label=sheet_assignee_label,
                    proforma_number=workflow_case.proforma_number,
                )
            else:
                sheet_sync_reason = str(sheet_sync_result.get("reason") or "").strip() or None
                _apply_sheet_sync_snapshot(
                    workflow_devices,
                    operation="pending",
                    sheet_result=sheet_sync_result,
                    assignee_id=sheet_assignee_id,
                    assignee_label=sheet_assignee_label,
                    proforma_number=workflow_case.proforma_number,
                )
        elif sheet_sync_warning:
            _apply_sheet_sync_snapshot(
                workflow_devices,
                operation="error",
                sheet_result=None,
                assignee_id=sheet_assignee_id,
                assignee_label=sheet_assignee_label,
                proforma_number=workflow_case.proforma_number,
                error=sheet_sync_warning,
            )

    message_parts = [
        (
            "Wybor urzadzen zapisany po stronie CTIP."
            if workflow_devices
            else "Usunieto powiazane urzadzenia ze sprawy CTIP."
        )
    ]
    if binding_items_payload:
        if binding_failures_count > 0:
            message_parts.append(
                "Uwaga: automat wiązania urządzeń zgłosił błędy "
                f"({binding_failures_count}). Wysłano alert do administratorów."
            )
        else:
            message_parts.append(
                f"Powiązano urządzenia z klientem MS ({len(binding_items_payload)})."
            )
    if sheet_sync_result and sheet_sync_result.get("enabled"):
        synced_count = int(sheet_sync_result.get("synced_count") or 0)
        if synced_count > 0:
            message_parts.append(
                f"Arkusz zsynchronizowany ({synced_count} urzadzen, rezerwacja: {sheet_assignee_label})."
            )
    elif sheet_sync_reason:
        message_parts.append(f"Synchronizacja arkusza pominieta ({sheet_sync_reason}).")
    if sheet_release_result and sheet_release_result.get("enabled"):
        released_count = int(sheet_release_result.get("released_count") or 0)
        if released_count > 0:
            message_parts.append(f"Zwolniono rezerwacje arkusza dla {released_count} urzadzen.")
    elif sheet_release_result and not sheet_release_result.get("enabled"):
        sheet_release_warning = str(sheet_release_result.get("reason") or "").strip() or None
    if sheet_sync_warning:
        message_parts.append(
            "Uwaga: nie udalo sie zsynchronizowac arkusza Google. "
            "Zapis pozostaje w CTIP, ale nic nie zapisano w arkuszu."
        )
    elif sheet_release_warning:
        message_parts.append("Uwaga: nie udalo sie zwolnic poprzednich rezerwacji arkusza.")
    await record_audit(
        session,
        user_id=admin_user.id,
        action="contracts_flow_devices_save",
        client_ip=admin_session.client_ip,
        payload={
            "form_request_id": item.id,
            "workflow_case_id": workflow_case.id,
            "rows": selected_rows,
            "selected_devices": selected_devices_meta,
            "prices": [
                {
                    "row": int(selected_item["row"]),
                    "source_type": normalize_workflow_device_source_type(
                        selected_item.get("source_type"),
                        default=WORKFLOW_DEVICE_SOURCE_FIREBIRD_WAREHOUSE,
                    ),
                    "price_net": selected_item["price_net"],
                    "price_gross": selected_item["price_gross"],
                }
                for selected_item in selected_payloads
            ],
            "sheet_assignee_id": sheet_assignee_id,
            "sheet_assignee_label": sheet_assignee_label,
            "sheet_sync_enabled": bool(sheet_sync_result and sheet_sync_result.get("enabled")),
            "sheet_sync_count": (
                int(sheet_sync_result.get("synced_count") or 0) if sheet_sync_result else 0
            ),
            "sheet_sync_reason": sheet_sync_reason,
            "sheet_sync_warning": sheet_sync_warning,
            "sheet_release_count": (
                int(sheet_release_result.get("released_count") or 0) if sheet_release_result else 0
            ),
            "sheet_release_warning": sheet_release_warning,
            "binding_items": binding_items_payload,
            "binding_alert": binding_alert_payload,
        },
    )
    await session.commit()

    return {
        "ok": True,
        "message": " ".join(part for part in message_parts if part),
        "workflow": serialize_workflow_case(workflow_case, workflow_devices),
        "selected_rows": selected_rows,
        "selected_devices": selected_devices_meta,
        "sheet_sync": sheet_sync_result,
        "sheet_release": sheet_release_result,
        "sheet_sync_warning": sheet_sync_warning,
        "sheet_release_warning": sheet_release_warning,
        "sheet_assignee_id": sheet_assignee_id,
        "sheet_assignee_label": sheet_assignee_label,
        "binding": {
            "items": binding_items_payload,
            "alert": binding_alert_payload,
        },
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
        signature_deadline_at=payload.signature_deadline_at,
        status_source="manual",
    )
    normalized_status = normalize_workflow_business_status(payload.business_status)
    if normalized_status in {
        WORKFLOW_BUSINESS_STATUS_APPROVED_ORDER,
        WORKFLOW_BUSINESS_STATUS_REJECTED_GRENKE,
    }:
        item.archive_due_at = datetime.now(UTC) + timedelta(days=ARCHIVE_DAYS_AFTER_DECISION)
    if normalized_status == WORKFLOW_BUSINESS_STATUS_REJECTED_GRENKE:
        workflow_case.resources_release_due_at = (
            workflow_case.resources_release_due_at
            or datetime.now(UTC) + timedelta(days=RESOURCE_RELEASE_DAYS_AFTER_REJECTION)
        )
    item.updated_at = datetime.now(UTC)
    workflow_devices = await list_form_workflow_devices(session, workflow_case_id=workflow_case.id)
    response_message_parts = ["Zapisano status sprawy FLOW."]
    binding_items_payload: list[dict[str, Any]] = []
    binding_alert_payload: dict[str, Any] | None = None
    sheet_sync_result: dict[str, Any] | None = None
    sheet_sync_warning: str | None = None
    sheet_sync_reason: str | None = None

    if normalized_status == WORKFLOW_BUSINESS_STATUS_APPROVED_ORDER:
        issuer_name = (
            " ".join(
                part.strip()
                for part in [admin_user.first_name or "", admin_user.last_name or ""]
                if part and part.strip()
            ).strip()
            or admin_user.email
        )
        binding_items, _ = await asyncio.to_thread(
            bind_devices_to_workflow_client,
            workflow_case=workflow_case,
            devices=workflow_devices,
            actor_label=issuer_name,
        )
        binding_items_payload = [item.as_dict() for item in binding_items]
        binding_by_device_id = {item.workflow_device_id: item for item in binding_items}
        for workflow_device in workflow_devices:
            apply_binding_snapshot(
                device=workflow_device,
                item=binding_by_device_id.get(workflow_device.id),
            )

        binding_failures = [item for item in binding_items if not item.ok]
        if binding_failures:
            binding_alert_payload = await notify_binding_issues_to_admins(
                session,
                workflow_case=workflow_case,
                form_request_id=item.id,
                failures=binding_failures,
                triggered_by_user_id=admin_user.id,
            )
            response_message_parts.append(
                "Status zatwierdzono, ale automat wiązania urządzeń zgłosił błędy "
                f"({len(binding_failures)}). Wysłano alert do administratorów."
            )
        elif binding_items:
            response_message_parts.append(
                f"Powiązano urządzenia z klientem MS ({len(binding_items)})."
            )
        else:
            response_message_parts.append("Brak urządzeń do powiązania na etapie zatwierdzenia.")

        if workflow_devices:
            current_workflow_payload = serialize_workflow_case(workflow_case, workflow_devices)
            current_sheet_state = current_workflow_payload.get("sheet_sync", {})
            sheet_assignee_label = (
                str(current_sheet_state.get("assignee_label") or "").strip() or issuer_name
            )
            sheet_payloads = [_build_sheet_device_payload(device) for device in workflow_devices]
            sheet_config = await load_workflow_sheet_runtime_config(session)
            try:
                with use_workflow_sheet_runtime_config(sheet_config):
                    sheet_sync_result = await asyncio.to_thread(
                        sync_workflow_devices_to_sheet,
                        devices=sheet_payloads,
                        assignee_label=sheet_assignee_label,
                        proforma_number=workflow_case.proforma_number or "",
                        form_request_id=item.id,
                        workflow_case_id=workflow_case.id,
                        business_status_label=workflow_business_status_label(
                            workflow_case.business_status
                        ),
                        reservation_client_name=_workflow_case_company_name(workflow_case),
                        overwrite_identity_fields=True,
                    )
                operation = "synced" if sheet_sync_result.get("enabled") else "pending"
                if not sheet_sync_result.get("enabled"):
                    sheet_sync_reason = str(sheet_sync_result.get("reason") or "").strip() or None
                _apply_sheet_sync_snapshot(
                    workflow_devices,
                    operation=operation,
                    sheet_result=sheet_sync_result,
                    assignee_id=_coerce_int(current_sheet_state.get("assignee_id")),
                    assignee_label=sheet_assignee_label,
                    proforma_number=workflow_case.proforma_number,
                )
            except Exception as exc:  # noqa: BLE001
                sheet_sync_warning = str(exc).strip() or (
                    f"{type(exc).__name__} podczas synchronizacji arkusza."
                )
                _apply_sheet_sync_snapshot(
                    workflow_devices,
                    operation="error",
                    sheet_result=None,
                    assignee_id=_coerce_int(current_sheet_state.get("assignee_id")),
                    assignee_label=sheet_assignee_label,
                    proforma_number=workflow_case.proforma_number,
                    error=sheet_sync_warning,
                )
            if sheet_sync_warning:
                response_message_parts.append(
                    "Uwaga: nie udało się zaktualizować arkusza po wiązaniu urządzeń."
                )
            elif sheet_sync_reason:
                response_message_parts.append(
                    f"Synchronizacja arkusza pominięta ({sheet_sync_reason})."
                )

    response_workflow = serialize_workflow_case(workflow_case, workflow_devices)
    if normalized_status == WORKFLOW_BUSINESS_STATUS_REJECTED_GRENKE:
        days = _days_until(workflow_case.resources_release_due_at)
        response_message_parts = [
            "Zapisano odmowę GRENKE. Zasoby pozostają w historii sprawy i zostaną "
            f"zwolnione automatycznie za {days} dni, jeżeli nie zostaną zwolnione ręcznie."
        ]

    await record_audit(
        session,
        user_id=admin_user.id,
        action="contracts_flow_status_save",
        client_ip=admin_session.client_ip,
        payload={
            "form_request_id": item.id,
            "workflow_case_id": workflow_case.id,
            "business_status": normalized_status,
            "signature_deadline_at": _to_iso(workflow_case.signature_deadline_at),
            "resources_release_due_at": _to_iso(workflow_case.resources_release_due_at),
            "archive_due_at": _to_iso(item.archive_due_at),
            "binding_items": binding_items_payload,
            "binding_alert": binding_alert_payload,
            "sheet_sync_enabled": bool(sheet_sync_result and sheet_sync_result.get("enabled")),
            "sheet_sync_count": (
                int(sheet_sync_result.get("synced_count") or 0) if sheet_sync_result else 0
            ),
            "sheet_sync_reason": sheet_sync_reason,
            "sheet_sync_warning": sheet_sync_warning,
        },
    )
    await session.commit()
    return {
        "ok": True,
        "message": " ".join(part for part in response_message_parts if part),
        "workflow": response_workflow,
        "binding": {
            "items": binding_items_payload,
            "alert": binding_alert_payload,
        },
        "sheet_sync": sheet_sync_result,
        "sheet_sync_warning": sheet_sync_warning,
        "archive_state": {
            "archive_due_at": _to_iso(item.archive_due_at),
            "days_to_archive": _days_until(item.archive_due_at),
        },
    }


@router.post("/forms/{form_id}/workflow/proforma", summary="Utworz proforme dla sprawy workflow")
async def contracts_form_workflow_proforma(
    form_id: int,
    payload: WorkflowProformaRequest | None = None,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Tworzy proforme w aktywnej konfiguracji Firebird dla formularza SUBMITTED."""
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
    try:
        sheet_assignee_id, sheet_assignee_label = await _resolve_sheet_assignee_selection(
            session=session,
            admin_user=admin_user,
            explicit_assignee_id=payload_data.sheet_assignee_id,
            fallback_label=issuer_name,
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

    firebird_config = await load_firebird_runtime_config(session)
    with use_firebird_runtime_config(firebird_config):
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
    sheet_sync_result: dict[str, Any] | None = None
    sheet_sync_warning: str | None = None
    sheet_sync_reason: str | None = None
    sheet_payloads = [_build_sheet_device_payload(device) for device in workflow_devices]
    if sheet_payloads:
        sheet_config = await load_workflow_sheet_runtime_config(session)
        try:
            with use_workflow_sheet_runtime_config(sheet_config):
                sheet_sync_result = await asyncio.to_thread(
                    sync_workflow_devices_to_sheet,
                    devices=sheet_payloads,
                    assignee_label=sheet_assignee_label,
                    proforma_number=result.document_number,
                    form_request_id=item.id,
                    workflow_case_id=workflow_case.id,
                    business_status_label=workflow_business_status_label(
                        workflow_case.business_status
                    ),
                    reservation_client_name=_workflow_case_company_name(workflow_case),
                )
            if sheet_sync_result.get("enabled"):
                _apply_sheet_sync_snapshot(
                    workflow_devices,
                    operation="synced",
                    sheet_result=sheet_sync_result,
                    assignee_id=sheet_assignee_id,
                    assignee_label=sheet_assignee_label,
                    proforma_number=result.document_number,
                )
            else:
                sheet_sync_reason = str(sheet_sync_result.get("reason") or "").strip() or None
                _apply_sheet_sync_snapshot(
                    workflow_devices,
                    operation="pending",
                    sheet_result=sheet_sync_result,
                    assignee_id=sheet_assignee_id,
                    assignee_label=sheet_assignee_label,
                    proforma_number=result.document_number,
                )
        except RuntimeError as exc:
            sheet_sync_warning = str(exc)
            _apply_sheet_sync_snapshot(
                workflow_devices,
                operation="error",
                sheet_result=None,
                assignee_id=sheet_assignee_id,
                assignee_label=sheet_assignee_label,
                proforma_number=result.document_number,
                error=sheet_sync_warning,
            )

    message_parts = [
        "Utworzono proforme w Menadzerze Serwisu: "
        f"{result.document_number} (odbiorca: {recipient_label})."
    ]
    if sheet_sync_result and sheet_sync_result.get("enabled"):
        synced_count = int(sheet_sync_result.get("synced_count") or 0)
        if synced_count > 0:
            message_parts.append(
                f"Arkusz zsynchronizowany ({synced_count} urzadzen, rezerwacja: {sheet_assignee_label})."
            )
    elif sheet_sync_reason:
        message_parts.append(f"Synchronizacja arkusza pominieta ({sheet_sync_reason}).")
    if sheet_sync_warning:
        message_parts.append("Uwaga: nie udalo sie zsynchronizowac arkusza Google.")

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
            "sheet_assignee_id": sheet_assignee_id,
            "sheet_assignee_label": sheet_assignee_label,
            "sheet_sync_enabled": bool(sheet_sync_result and sheet_sync_result.get("enabled")),
            "sheet_sync_count": (
                int(sheet_sync_result.get("synced_count") or 0) if sheet_sync_result else 0
            ),
            "sheet_sync_reason": sheet_sync_reason,
            "sheet_sync_warning": sheet_sync_warning,
        },
    )
    await session.commit()

    return {
        "ok": True,
        "created": True,
        "message": " ".join(part for part in message_parts if part),
        "proforma_firebird_id": result.id_faktura_table,
        "proforma_number": result.document_number,
        "preview_url": result.preview_url,
        "for_bank": bool(payload_data.for_bank),
        "recipient_client_id": recipient_client_id,
        "sheet_assignee_id": sheet_assignee_id,
        "sheet_assignee_label": sheet_assignee_label,
        "sheet_sync": sheet_sync_result,
        "sheet_sync_warning": sheet_sync_warning,
        "workflow": serialize_workflow_case(workflow_case, workflow_devices),
    }


@router.post(
    "/forms/{form_id}/workflow/proforma-reset",
    summary="Usun zapisana proforme ze sprawy workflow",
)
async def contracts_form_workflow_proforma_reset(
    form_id: int,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Usuwa proforme z Firebird i arkusza, a potem czyści stan CTIP."""
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
            detail="Brak zapisanej sprawy workflow dla formularza.",
        )
    if not workflow_case.proforma_firebird_id or not workflow_case.proforma_number:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Brak zapisanej proformy do usuniecia.",
        )

    workflow_devices = await list_form_workflow_devices(session, workflow_case_id=workflow_case.id)
    firebird_delete_result: dict[str, Any] | None = None
    sheet_clear_result: dict[str, Any] | None = None
    sheet_clear_reason: str | None = None
    sheet_clear_warning: str | None = None

    firebird_config = await load_firebird_runtime_config(session)
    with use_firebird_runtime_config(firebird_config):
        try:
            delete_result = await asyncio.to_thread(
                delete_proforma_from_firebird,
                int(workflow_case.proforma_firebird_id),
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
    if not delete_result.deleted:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Nie znaleziono proformy w aktywnej bazie Firebird dla wskazanego ID. "
                "Sprawdz konfiguracje runtime Firebird albo zgodnosc ID dokumentu."
            ),
        )
    firebird_delete_result = {
        "deleted": bool(delete_result.deleted),
        "deleted_lines": int(delete_result.deleted_lines or 0),
        "pdf_deleted": bool(delete_result.pdf_deleted),
        "proforma_firebird_id": int(delete_result.id_faktura_table),
    }

    sheet_payloads = _build_sheet_release_payloads(workflow_devices, include_all=True)
    if sheet_payloads:
        sheet_config = await load_workflow_sheet_runtime_config(session)
        try:
            with use_workflow_sheet_runtime_config(sheet_config):
                sheet_clear_result = await asyncio.to_thread(
                    clear_workflow_proforma_from_sheet,
                    devices=sheet_payloads,
                )
            if sheet_clear_result.get("enabled"):
                _apply_sheet_sync_snapshot(
                    workflow_devices,
                    operation="proforma_cleared",
                    sheet_result=sheet_clear_result,
                    assignee_id=None,
                    assignee_label=None,
                    proforma_number="",
                )
            else:
                sheet_clear_reason = str(sheet_clear_result.get("reason") or "").strip() or None
        except RuntimeError as exc:
            sheet_clear_warning = str(exc)
        if (
            sheet_clear_result
            and sheet_clear_result.get("enabled")
            and int(sheet_clear_result.get("cleared_count") or 0) < len(sheet_payloads)
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Arkusz Google nie odnalazl wszystkich wierszy dla tej proformy. "
                    "Usuwanie zostalo przerwane, aby nie zostawic niespójnego stanu."
                ),
            )
    if workflow_devices and not (sheet_clear_result and sheet_clear_result.get("enabled")):
        _apply_sheet_sync_snapshot(
            workflow_devices,
            operation="proforma_cleared",
            sheet_result=sheet_clear_result,
            assignee_id=None,
            assignee_label=None,
            proforma_number="",
            error=sheet_clear_warning,
        )

    workflow_case = await clear_form_workflow_proforma(
        session,
        workflow_case=workflow_case,
        updated_by=admin_user.id,
    )
    await record_audit(
        session,
        user_id=admin_user.id,
        action="contracts_flow_proforma_reset",
        client_ip=admin_session.client_ip,
        payload={
            "form_request_id": item.id,
            "workflow_case_id": workflow_case.id,
            "firebird_delete": firebird_delete_result,
            "sheet_clear_enabled": bool(sheet_clear_result and sheet_clear_result.get("enabled")),
            "sheet_clear_count": (
                int(sheet_clear_result.get("cleared_count") or 0) if sheet_clear_result else 0
            ),
            "sheet_clear_reason": sheet_clear_reason,
            "sheet_clear_warning": sheet_clear_warning,
        },
    )
    await session.commit()

    message_parts = [
        "Usunieto proforme z Menadzera Serwisu, zwolniono numer dokumentu i wyczyszczono wpis w arkuszu Google."
    ]
    if sheet_clear_reason:
        message_parts.append(f"Czyszczenie arkusza pominiete ({sheet_clear_reason}).")
    if sheet_clear_warning:
        message_parts.append("Uwaga: nie udalo sie zaktualizowac arkusza Google.")
    message_parts.append("Rezerwacja urzadzen pozostala aktywna.")

    return {
        "ok": True,
        "message": " ".join(message_parts),
        "firebird_delete": firebird_delete_result,
        "sheet_clear": sheet_clear_result,
        "sheet_clear_warning": sheet_clear_warning,
        "workflow": serialize_workflow_case(workflow_case, workflow_devices),
    }


@router.post(
    "/forms/{form_id}/workflow/sheet-sync",
    summary="Synchronizuj arkusz urzadzen dla sprawy workflow",
)
async def contracts_form_workflow_sheet_sync(
    form_id: int,
    payload: WorkflowSheetSyncRequest | None = None,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Wymusza synchronizacje rezerwacji urządzeń do arkusza Google."""
    payload_data = payload or WorkflowSheetSyncRequest()
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
            detail="Brak zapisanej sprawy workflow dla formularza.",
        )
    workflow_devices = await list_form_workflow_devices(session, workflow_case_id=workflow_case.id)
    if not workflow_devices:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Brak urzadzen zapisanych w sprawie workflow.",
        )
    if not workflow_case.proforma_number:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Najpierw utworz proforme dla tej sprawy.",
        )

    issuer_name = (
        " ".join(
            part.strip()
            for part in [admin_user.first_name or "", admin_user.last_name or ""]
            if part and part.strip()
        ).strip()
        or admin_user.email
    )
    try:
        sheet_assignee_id, sheet_assignee_label = await _resolve_sheet_assignee_selection(
            session=session,
            admin_user=admin_user,
            explicit_assignee_id=payload_data.sheet_assignee_id,
            fallback_label=issuer_name,
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

    sheet_payloads = [_build_sheet_device_payload(device) for device in workflow_devices]
    sheet_config = await load_workflow_sheet_runtime_config(session)
    try:
        with use_workflow_sheet_runtime_config(sheet_config):
            sheet_sync_result = await asyncio.to_thread(
                sync_workflow_devices_to_sheet,
                devices=sheet_payloads,
                assignee_label=sheet_assignee_label,
                proforma_number=workflow_case.proforma_number,
                form_request_id=item.id,
                workflow_case_id=workflow_case.id,
                business_status_label=workflow_business_status_label(workflow_case.business_status),
                reservation_client_name=_workflow_case_company_name(workflow_case),
            )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Nie udalo sie zsynchronizowac arkusza: {exc}",
        ) from exc

    sync_reason = str(sheet_sync_result.get("reason") or "").strip() or None
    operation = "synced" if sheet_sync_result.get("enabled") else "pending"
    _apply_sheet_sync_snapshot(
        workflow_devices,
        operation=operation,
        sheet_result=sheet_sync_result,
        assignee_id=sheet_assignee_id,
        assignee_label=sheet_assignee_label,
        proforma_number=workflow_case.proforma_number,
    )
    synced_count = int(sheet_sync_result.get("synced_count") or 0)
    message = "Arkusz zsynchronizowany."
    if sheet_sync_result.get("enabled"):
        message = f"Arkusz zsynchronizowany ({synced_count} urzadzen)."
    elif sync_reason:
        message = f"Synchronizacja arkusza pominieta ({sync_reason})."

    await record_audit(
        session,
        user_id=admin_user.id,
        action="contracts_flow_sheet_sync",
        client_ip=admin_session.client_ip,
        payload={
            "form_request_id": item.id,
            "workflow_case_id": workflow_case.id,
            "sheet_assignee_id": sheet_assignee_id,
            "sheet_assignee_label": sheet_assignee_label,
            "sheet_sync_enabled": bool(sheet_sync_result.get("enabled")),
            "sheet_sync_count": synced_count,
            "sheet_sync_reason": sync_reason,
        },
    )
    await session.commit()
    return {
        "ok": True,
        "message": message,
        "sheet_sync": sheet_sync_result,
        "workflow": serialize_workflow_case(workflow_case, workflow_devices),
    }


@router.post(
    "/forms/{form_id}/workflow/sheet-release",
    summary="Zwolnij rezerwacje arkusza dla sprawy workflow",
)
async def contracts_form_workflow_sheet_release(
    form_id: int,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Czyści rezerwację GRENKE dla urządzeń przypisanych do sprawy."""
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
            detail="Brak zapisanej sprawy workflow dla formularza.",
        )
    workflow_devices = await list_form_workflow_devices(session, workflow_case_id=workflow_case.id)
    if not workflow_devices:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Brak urzadzen zapisanych w sprawie workflow.",
        )

    sheet_payloads = _build_sheet_release_payloads(workflow_devices, include_all=True)
    if not sheet_payloads:
        return {
            "ok": True,
            "message": "Brak danych rezerwacji arkusza do zwolnienia.",
            "sheet_release": {
                "enabled": False,
                "reason": "Brak danych synchronizacji w zapisanym snapshotcie urzadzen.",
                "released_count": 0,
                "rows": [],
            },
            "workflow": serialize_workflow_case(workflow_case, workflow_devices),
        }

    sheet_config = await load_workflow_sheet_runtime_config(session)
    try:
        with use_workflow_sheet_runtime_config(sheet_config):
            sheet_release_result = await asyncio.to_thread(
                release_workflow_devices_from_sheet,
                devices=sheet_payloads,
            )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Nie udalo sie zwolnic rezerwacji arkusza: {exc}",
        ) from exc

    current_sheet_sync = serialize_workflow_case(workflow_case, workflow_devices).get(
        "sheet_sync", {}
    )
    _apply_sheet_sync_snapshot(
        workflow_devices,
        operation="released",
        sheet_result=sheet_release_result,
        assignee_id=_coerce_int(current_sheet_sync.get("assignee_id")),
        assignee_label=str(current_sheet_sync.get("assignee_label") or "").strip() or None,
        proforma_number=workflow_case.proforma_number,
    )
    released_count = int(sheet_release_result.get("released_count") or 0)
    release_reason = str(sheet_release_result.get("reason") or "").strip() or None
    message = (
        f"Zwolniono rezerwacje arkusza dla {released_count} urzadzen."
        if sheet_release_result.get("enabled")
        else (
            f"Zwolnienie rezerwacji pominiete ({release_reason})."
            if release_reason
            else "Zwolnienie rezerwacji pominiete."
        )
    )

    await record_audit(
        session,
        user_id=admin_user.id,
        action="contracts_flow_sheet_release",
        client_ip=admin_session.client_ip,
        payload={
            "form_request_id": item.id,
            "workflow_case_id": workflow_case.id,
            "sheet_release_enabled": bool(sheet_release_result.get("enabled")),
            "sheet_release_count": released_count,
            "sheet_release_reason": release_reason,
        },
    )
    await session.commit()
    return {
        "ok": True,
        "message": message,
        "sheet_release": sheet_release_result,
        "workflow": serialize_workflow_case(workflow_case, workflow_devices),
    }


@router.post(
    "/forms/{form_id}/workflow/release-resources",
    summary="Zwolnij zasoby po odmowie GRENKE",
)
async def contracts_form_workflow_release_resources(
    form_id: int,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Zwalnia rezerwacje i usuwa proformę, ale zostawia pełną historię sprawy."""
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Formularz nie istnieje.")
    if item.status != "SUBMITTED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Zasoby workflow są dostępne tylko dla formularzy SUBMITTED.",
        )

    workflow_case = await get_form_workflow_case(session, form_request_id=item.id)
    if workflow_case is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Brak zapisanej sprawy workflow dla formularza.",
        )

    workflow_devices = await list_form_workflow_devices(session, workflow_case_id=workflow_case.id)
    sheet_release_result: dict[str, Any] | None = None
    sheet_release_warning: str | None = None
    firebird_delete_result: dict[str, Any] | None = None
    sheet_clear_result: dict[str, Any] | None = None
    sheet_clear_warning: str | None = None

    sheet_payloads = _build_sheet_release_payloads(workflow_devices, include_all=True)
    current_sheet_sync = serialize_workflow_case(workflow_case, workflow_devices).get(
        "sheet_sync", {}
    )
    if sheet_payloads:
        sheet_config = await load_workflow_sheet_runtime_config(session)
        try:
            with use_workflow_sheet_runtime_config(sheet_config):
                sheet_release_result = await asyncio.to_thread(
                    release_workflow_devices_from_sheet,
                    devices=sheet_payloads,
                )
        except RuntimeError as exc:
            sheet_release_warning = str(exc)
        _apply_sheet_sync_snapshot(
            workflow_devices,
            operation="released" if sheet_release_warning is None else "error",
            sheet_result=sheet_release_result,
            assignee_id=_coerce_int(current_sheet_sync.get("assignee_id")),
            assignee_label=str(current_sheet_sync.get("assignee_label") or "").strip() or None,
            proforma_number=workflow_case.proforma_number,
            error=sheet_release_warning,
        )

    previous_proforma = {
        "proforma_firebird_id": workflow_case.proforma_firebird_id,
        "proforma_number": workflow_case.proforma_number,
        "proforma_pdf_path": workflow_case.proforma_pdf_path,
    }
    if workflow_case.proforma_firebird_id and workflow_case.proforma_number:
        firebird_config = await load_firebird_runtime_config(session)
        with use_firebird_runtime_config(firebird_config):
            try:
                delete_result = await asyncio.to_thread(
                    delete_proforma_from_firebird,
                    int(workflow_case.proforma_firebird_id),
                )
            except ValueError as exc:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
            except RuntimeError as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=str(exc),
                ) from exc
        firebird_delete_result = {
            "deleted": bool(delete_result.deleted),
            "deleted_lines": int(delete_result.deleted_lines or 0),
            "pdf_deleted": bool(delete_result.pdf_deleted),
            "proforma_firebird_id": int(delete_result.id_faktura_table),
        }
        if sheet_payloads:
            sheet_config = await load_workflow_sheet_runtime_config(session)
            try:
                with use_workflow_sheet_runtime_config(sheet_config):
                    sheet_clear_result = await asyncio.to_thread(
                        clear_workflow_proforma_from_sheet,
                        devices=sheet_payloads,
                    )
            except RuntimeError as exc:
                sheet_clear_warning = str(exc)
        workflow_case = await clear_form_workflow_proforma(
            session,
            workflow_case=workflow_case,
            updated_by=admin_user.id,
        )

    workflow_case = await mark_workflow_resources_released(
        session,
        workflow_case=workflow_case,
        updated_by=admin_user.id,
        status_source="manual_release",
        note="Ręcznie zwolniono zasoby po odmowie GRENKE.",
    )
    await record_audit(
        session,
        user_id=admin_user.id,
        action="contracts_flow_resources_release",
        client_ip=admin_session.client_ip,
        payload={
            "form_request_id": item.id,
            "workflow_case_id": workflow_case.id,
            "previous_proforma": previous_proforma,
            "sheet_release": sheet_release_result,
            "sheet_release_warning": sheet_release_warning,
            "firebird_delete": firebird_delete_result,
            "sheet_clear": sheet_clear_result,
            "sheet_clear_warning": sheet_clear_warning,
        },
    )
    await session.commit()

    return {
        "ok": True,
        "message": "Zwolniono rezerwacje i usunięto aktywną proformę. Historia formularza została zachowana.",
        "sheet_release": sheet_release_result,
        "sheet_release_warning": sheet_release_warning,
        "firebird_delete": firebird_delete_result,
        "sheet_clear": sheet_clear_result,
        "sheet_clear_warning": sheet_clear_warning,
        "workflow": serialize_workflow_case(workflow_case, workflow_devices),
    }


@router.post("/forms/{form_id}/archive", summary="Przenies formularz do archiwum GenForm")
async def contracts_form_archive(
    form_id: int,
    payload: WorkflowArchiveRequest | None = None,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Przenosi formularz do wybranej sekcji archiwum bez usuwania historii."""
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Formularz nie istnieje.")
    workflow_case = await get_form_workflow_case(session, form_request_id=item.id)
    workflow_devices = (
        await list_form_workflow_devices(session, workflow_case_id=workflow_case.id)
        if workflow_case is not None
        else []
    )
    workflow_payload = serialize_workflow_case(workflow_case, workflow_devices)
    target_bucket = (payload.bucket if payload else None) or _archive_bucket_for_form(
        item,
        workflow_payload,
    )
    if target_bucket is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ten formularz nie ma jeszcze docelowej sekcji archiwum.",
        )
    item.archive_bucket = target_bucket
    item.archived_at = datetime.now(UTC)
    item.updated_at = datetime.now(UTC)
    await record_audit(
        session,
        user_id=admin_user.id,
        action="contracts_form_archive",
        client_ip=admin_session.client_ip,
        payload={"form_request_id": item.id, "archive_bucket": target_bucket},
    )
    await session.commit()
    return {
        "ok": True,
        "message": "Formularz przeniesiono do archiwum.",
        "archive_state": {
            "bucket": item.archive_bucket,
            "archived_at": _to_iso(item.archived_at),
            "archive_due_at": _to_iso(item.archive_due_at),
        },
    }


@router.post(
    "/forms/{form_id}/archive/extend",
    summary="Przedluz termin automatycznej archiwizacji formularza",
)
async def contracts_form_archive_extend(
    form_id: int,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Przedłuża termin automatycznej archiwizacji o 7 dni."""
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Formularz nie istnieje.")
    base = item.archive_due_at or datetime.now(UTC)
    if base.tzinfo is None:
        base = base.replace(tzinfo=UTC)
    if base < datetime.now(UTC):
        base = datetime.now(UTC)
    item.archive_due_at = base + timedelta(days=7)
    item.updated_at = datetime.now(UTC)
    await record_audit(
        session,
        user_id=admin_user.id,
        action="contracts_form_archive_extend",
        client_ip=admin_session.client_ip,
        payload={"form_request_id": item.id, "archive_due_at": _to_iso(item.archive_due_at)},
    )
    await session.commit()
    return {
        "ok": True,
        "message": "Termin archiwizacji przedłużono o 7 dni.",
        "archive_state": {
            "archive_due_at": _to_iso(item.archive_due_at),
            "days_to_archive": _days_until(item.archive_due_at),
        },
    }


@router.post("/workflow/mailbox-sync", summary="Uruchom synchronizacje mailbox -> FLOW")
async def contracts_workflow_mailbox_sync(
    payload: WorkflowMailboxSyncRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Uruchamia skrypt synchronizacji mailboxa i zwraca zwięzły raport wykonania."""
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

    repo_root = Path(__file__).resolve().parents[3]
    script_path = repo_root / "scripts" / "contracts_mailbox_sync.py"
    if not script_path.exists():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Brak skryptu synchronizacji mailboxa: {script_path}",
        )

    try:
        result = await asyncio.to_thread(
            run_mailbox_sync_subprocess,
            limit=payload.limit,
            folder=payload.folder,
            reprocess=payload.reprocess,
            dry_run=payload.dry_run,
            timeout_seconds=payload.timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        await record_audit(
            session,
            user_id=admin_user.id,
            action="contracts_mailbox_sync_trigger",
            client_ip=admin_session.client_ip,
            payload={
                "limit": payload.limit,
                "folder": payload.folder,
                "dry_run": payload.dry_run,
                "reprocess": payload.reprocess,
                "timeout_seconds": payload.timeout_seconds,
                "result": "timeout",
                "error": str(exc),
            },
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Synchronizacja mailboxa przekroczyla limit czasu.",
        ) from exc

    stdout_tail = _tail_text(result.stdout or "")
    stderr_tail = _tail_text(result.stderr or "")
    summary = parse_mailbox_sync_summary(result.stdout or "")

    await record_audit(
        session,
        user_id=admin_user.id,
        action="contracts_mailbox_sync_trigger",
        client_ip=admin_session.client_ip,
        payload={
            "limit": payload.limit,
            "folder": payload.folder,
            "dry_run": payload.dry_run,
            "reprocess": payload.reprocess,
            "timeout_seconds": payload.timeout_seconds,
            "exit_code": result.returncode,
            "summary": summary,
        },
    )
    await session.commit()

    if result.returncode != 0:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "message": "Synchronizacja mailboxa zakonczona bledem.",
                "exit_code": result.returncode,
                "summary": summary,
                "stdout_tail": stdout_tail,
                "stderr_tail": stderr_tail,
            },
        )

    return {
        "ok": True,
        "message": "Synchronizacja mailboxa zakonczona powodzeniem.",
        "exit_code": result.returncode,
        "summary": summary,
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
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
        firebird_config = await load_firebird_runtime_config(session)

        if payload.action == "utworz_klienta":
            with use_firebird_runtime_config(firebird_config):
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
                message = f"Utworzono klienta w Firebird: ID {result.match.id_klient}."
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

            with use_firebird_runtime_config(firebird_config):
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
            firebird_config = await load_firebird_runtime_config(session)
            with use_firebird_runtime_config(firebird_config):
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
