"""Logika modułu obsługi dostaw, odbiorów i kalendarza końców umów GRENKE."""

from __future__ import annotations

import html
import re
import zipfile
from collections.abc import Iterable
from datetime import UTC, date, datetime
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from typing import Any

from sqlalchemy import Text, cast, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models import (
    AdminUser,
    DeliveryCase,
    DeliveryCaseDevice,
    DeliveryCaseFile,
    DeliveryCaseTask,
    DeliveryDocumentTemplate,
    FormRequest,
    FormWorkflowCase,
    FormWorkflowDevice,
    GrenkeContractEnd,
    SmsOut,
)
from app.services import admin_users
from app.services.contract_pdf_parser import extract_contract_data_from_text, extract_text_from_pdf
from app.services.contracts_workflow import (
    build_workflow_device_key,
    normalize_workflow_device_source_type,
    resolve_workflow_grenke_contract_start_date,
)
from app.services.email_client import send_smtp_message

DELIVERY_SOURCE_GRENKE = "grenke"
DELIVERY_SOURCE_MANUAL = "manual"
DELIVERY_CASE_TYPE_DELIVERY = "delivery"
DELIVERY_CASE_TYPE_PICKUP = "pickup"
DELIVERY_STATUS_NEW = "new"
DELIVERY_STATUS_PLANNED = "planned"
DELIVERY_STATUS_DONE = "done"
DELIVERY_STATUS_CANCELLED = "cancelled"
DELIVERY_DEVICE_ROLE_DELIVERY = "delivery"
DELIVERY_DEVICE_ROLE_PICKUP = "pickup"
CONTRACT_END_PENDING = "pending_confirmation"
CONTRACT_END_CONFIRMED = "confirmed"
CONTRACT_END_CANCELLED = "cancelled"
CONTRACT_REMINDER_THRESHOLDS = (60, 30, 7)

_DATE_PATTERNS = (
    re.compile(
        r"(?i)(?:data\s+zako[nń]czenia|koniec\s+umowy|umowa\s+do|obowi[aą]zuje\s+do)"
        r"[^0-9]{0,60}(\d{1,2}[.\-/]\d{1,2}[.\-/]\d{4})"
    ),
    re.compile(
        r"(?i)(?:okres\s+(?:umowy|obowi[aą]zywania)).{0,120}?\bdo\b"
        r"[^0-9]{0,40}(\d{1,2}[.\-/]\d{1,2}[.\-/]\d{4})"
    ),
)
_DURATION_KEYS = (
    "contract_duration_months",
    "lease_duration_months",
    "leasing_duration_months",
    "duration_months",
    "okres_umowy_miesiace",
)
_END_DATE_KEYS = (
    "contract_end_date",
    "grenke_contract_end_date",
    "lease_end_date",
    "end_date",
    "data_konca_umowy",
)
_DOCUMENT_TYPE_BY_NAME = (
    ("protok", "protocol"),
    ("najmu", "rental_contract"),
    ("wynajmu", "rental_contract"),
    ("serwis", "service_contract"),
    ("aneks", "annex"),
    ("wypowiedzenie", "termination"),
    ("liczniki", "meters"),
)
_ALLOWED_TEMPLATE_SUFFIXES = {".doc", ".docx"}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_repo_path(value: str | None, *, fallback: str) -> Path:
    raw = Path(str(value or fallback)).expanduser()
    return raw if raw.is_absolute() else _repo_root() / raw


def delivery_files_root() -> Path:
    """Zwraca katalog plików modułu dostaw."""
    return _resolve_repo_path(settings.delivery_files_root, fallback="inbox/delivery/files")


def delivery_templates_root() -> Path:
    """Zwraca katalog wzorów dokumentów dostaw."""
    return _resolve_repo_path(settings.delivery_document_templates_root, fallback="inbox/doku")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _to_iso(value: datetime | date | None) -> str | None:
    return value.isoformat() if value else None


def _clean_text(value: Any, *, max_len: int | None = None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    text = re.sub(r"\s+", " ", text)
    return text[:max_len] if max_len else text


def _parse_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def _parse_int(value: Any) -> int | None:
    if value is None:
        return None
    match = re.search(r"\d+", str(value))
    if not match:
        return None
    parsed = int(match.group(0))
    return parsed if parsed > 0 else None


def _add_months(base: date, months: int) -> date:
    month_index = base.month - 1 + months
    year = base.year + month_index // 12
    month = month_index % 12 + 1
    days_in_month = [
        31,
        29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    ]
    day = min(base.day, days_in_month[month - 1])
    return date(year, month, day)


def _walk_values(payload: Any) -> Iterable[Any]:
    if isinstance(payload, dict):
        for key, value in payload.items():
            yield key, value
            yield from _walk_values(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from _walk_values(item)


def _safe_segment(value: str | None, *, fallback: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r'[<>:"/\\|?*\x00-\x1F]+', "_", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text or fallback


def _document_type_from_name(name: str) -> str:
    normalized = name.casefold()
    for token, doc_type in _DOCUMENT_TYPE_BY_NAME:
        if token in normalized:
            return doc_type
    return "other"


def infer_grenke_contract_end(
    workflow_case: FormWorkflowCase,
) -> tuple[date | None, str | None, str | None]:
    """Próbuje ustalić datę końca umowy i numer umowy z danych workflow."""
    snapshot = (
        workflow_case.client_payload_snapshot
        if isinstance(workflow_case.client_payload_snapshot, dict)
        else {}
    )

    for key, value in _walk_values(snapshot):
        normalized_key = str(key or "").strip().lower()
        if normalized_key in _END_DATE_KEYS:
            parsed = _parse_date(value)
            if parsed:
                return parsed, "prefill z pola danych workflow", None

    duration_months = None
    for key, value in _walk_values(snapshot):
        normalized_key = str(key or "").strip().lower()
        if normalized_key in _DURATION_KEYS:
            duration_months = _parse_int(value)
            if duration_months:
                break
    grenke_contract_start_date = resolve_workflow_grenke_contract_start_date(workflow_case)
    if duration_months and grenke_contract_start_date:
        return (
            _add_months(grenke_contract_start_date, duration_months),
            f"prefill: początek umowy GRENKE + {duration_months} mies.",
            None,
        )

    for file_meta in archived_contract_files_from_snapshot(snapshot):
        path_value = _clean_text(file_meta.get("path"), max_len=1000)
        if not path_value:
            continue
        path = Path(path_value)
        if not path.exists():
            continue
        try:
            text = extract_text_from_pdf(path)
        except Exception:  # noqa: BLE001
            continue
        contract_data = extract_contract_data_from_text(text)
        for pattern in _DATE_PATTERNS:
            match = pattern.search(text)
            if match:
                parsed = _parse_date(match.group(1))
                if parsed:
                    return parsed, f"prefill z PDF: {path.name}", contract_data.contract_number
        if contract_data.contract_number:
            return None, f"PDF bez daty końca: {path.name}", contract_data.contract_number

    return None, None, None


def archived_contract_files_from_snapshot(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Wyciąga metadane plików umów GRENKE zapisane przez synchronizację skrzynki."""
    meta = snapshot.get("_mailbox_meta") if isinstance(snapshot, dict) else None
    archive_files = meta.get("archived_contract_files") if isinstance(meta, dict) else None
    if not isinstance(archive_files, list):
        return []
    output: list[dict[str, Any]] = []
    for file_meta in archive_files:
        if not isinstance(file_meta, dict):
            continue
        if file_meta.get("kind") not in {"decrypted_contract_pdf", "encrypted_contract_pdf"}:
            continue
        output.append(file_meta)
    return output


def _company_from_workflow(
    workflow_case: FormWorkflowCase, form_request: FormRequest | None
) -> tuple[str | None, str | None, str | None, str | None]:
    snapshot = (
        workflow_case.client_payload_snapshot
        if isinstance(workflow_case.client_payload_snapshot, dict)
        else {}
    )
    return (
        _clean_text(
            snapshot.get("company_name") or getattr(form_request, "customer_name", None),
            max_len=500,
        ),
        _clean_text(snapshot.get("company_nip"), max_len=32),
        _clean_text(
            snapshot.get("company_email") or getattr(form_request, "customer_email", None),
            max_len=250,
        ),
        _clean_text(
            snapshot.get("company_phone") or getattr(form_request, "customer_phone", None),
            max_len=80,
        ),
    )


def serialize_delivery_task(task: DeliveryCaseTask) -> dict[str, Any]:
    """Serializuje zadanie sprawy dostawy."""
    return {
        "id": task.id,
        "task_type": task.task_type,
        "status": task.status,
        "title": task.title,
        "due_date": _to_iso(task.due_date),
        "due_time_window": task.due_time_window,
        "assignee_user_id": task.assignee_user_id,
        "notes": task.notes,
        "created_at": _to_iso(task.created_at),
        "updated_at": _to_iso(task.updated_at),
    }


def serialize_delivery_file(file_item: DeliveryCaseFile) -> dict[str, Any]:
    """Serializuje plik sprawy dostawy."""
    return {
        "id": file_item.id,
        "file_type": file_item.file_type,
        "source": file_item.source,
        "file_name": file_item.file_name,
        "original_name": file_item.original_name,
        "content_type": file_item.content_type,
        "size_bytes": file_item.size_bytes,
        "created_at": _to_iso(file_item.created_at),
        "download_url": f"/admin/delivery/files/{file_item.id}/download",
    }


def serialize_mailbox_file(
    case: DeliveryCase, index: int, file_meta: dict[str, Any]
) -> dict[str, Any]:
    """Serializuje plik umowy GRENKE zapisany przez synchronizację mailboxa."""
    path = Path(str(file_meta.get("path") or ""))
    return {
        "id": f"mailbox:{index}",
        "file_type": "grenke_contract",
        "source": "mailbox",
        "file_name": str(file_meta.get("file_name") or path.name or "umowa.pdf"),
        "original_name": file_meta.get("original_name"),
        "description": file_meta.get("description"),
        "kind": file_meta.get("kind"),
        "exists": path.exists(),
        "created_at": file_meta.get("saved_at_utc"),
        "download_url": f"/admin/delivery/cases/{case.id}/mailbox-files/{index}/download",
    }


def serialize_delivery_device(device: DeliveryCaseDevice) -> dict[str, Any]:
    """Serializuje urządzenie przypisane do sprawy dostawy albo odbioru."""
    return {
        "id": device.id,
        "workflow_device_id": device.workflow_device_id,
        "producer": device.producer,
        "model": device.model,
        "serial": device.serial,
        "ewidencja": device.ewidencja,
        "firebird_machine_id": device.firebird_machine_id,
        "device_role": getattr(device, "device_role", None) or DELIVERY_DEVICE_ROLE_DELIVERY,
        "source_type": device.source_type,
        "source_row": device.source_row,
        "snapshot": device.snapshot or {},
    }


def serialize_delivery_case(
    case: DeliveryCase, *, include_mailbox_files: bool = True
) -> dict[str, Any]:
    """Zwraca słownik bezpieczny do API."""
    mailbox_files: list[dict[str, Any]] = []
    if include_mailbox_files and case.workflow_case is not None:
        snapshot = (
            case.workflow_case.client_payload_snapshot
            if isinstance(case.workflow_case.client_payload_snapshot, dict)
            else {}
        )
        mailbox_files = [
            serialize_mailbox_file(case, index, file_meta)
            for index, file_meta in enumerate(archived_contract_files_from_snapshot(snapshot))
        ]
    return {
        "id": case.id,
        "source": case.source,
        "case_type": getattr(case, "case_type", None) or DELIVERY_CASE_TYPE_DELIVERY,
        "status": case.status,
        "title": case.title,
        "form_request_id": case.form_request_id,
        "workflow_case_id": case.workflow_case_id,
        "firebird_client_id": case.firebird_client_id,
        "customer_name": case.customer_name,
        "customer_nip": case.customer_nip,
        "customer_email": case.customer_email,
        "customer_phone": case.customer_phone,
        "delivery_date": _to_iso(case.delivery_date),
        "delivery_time_window": case.delivery_time_window,
        "delivery_contact_name": case.delivery_contact_name,
        "delivery_contact_phone": case.delivery_contact_phone,
        "delivery_notes": case.delivery_notes,
        "service_notes": case.service_notes,
        "created_at": _to_iso(case.created_at),
        "updated_at": _to_iso(case.updated_at),
        "devices": [serialize_delivery_device(device) for device in getattr(case, "devices", [])],
        "tasks": [serialize_delivery_task(task) for task in getattr(case, "tasks", [])],
        "files": [serialize_delivery_file(file_item) for file_item in getattr(case, "files", [])],
        "mailbox_files": mailbox_files,
        "snapshot": case.snapshot or {},
    }


def build_contract_notification_schedule(item: GrenkeContractEnd) -> list[dict[str, Any]]:
    """Buduje widoczny harmonogram progów powiadomień dla umowy GRENKE."""
    end_date = item.confirmed_end_date
    if not end_date:
        return []
    history = item.notification_history if isinstance(item.notification_history, list) else []
    output: list[dict[str, Any]] = []
    for days in CONTRACT_REMINDER_THRESHOLDS:
        notify_date = _add_months(end_date, 0)
        # Prosty zapis bez dodatkowych zależności: data końca minus dni.
        notify_date = date.fromordinal(end_date.toordinal() - days)
        key = f"{days}:{end_date.isoformat()}"
        sent_entry = next(
            (entry for entry in history if isinstance(entry, dict) and entry.get("key") == key),
            None,
        )
        output.append(
            {
                "days_left": days,
                "notify_date": notify_date.isoformat(),
                "sent": sent_entry is not None,
                "sent_at": sent_entry.get("sent_at") if isinstance(sent_entry, dict) else None,
                "sms_queued": sent_entry.get("sms_queued") if isinstance(sent_entry, dict) else 0,
                "emails_sent": sent_entry.get("emails_sent") if isinstance(sent_entry, dict) else 0,
            }
        )
    return output


def serialize_grenke_contract_end(item: GrenkeContractEnd) -> dict[str, Any]:
    """Serializuje wpis kalendarza końca umowy GRENKE."""
    return {
        "id": item.id,
        "delivery_case_id": item.delivery_case_id,
        "form_request_id": item.form_request_id,
        "workflow_case_id": item.workflow_case_id,
        "status": item.status,
        "grenke_contract_start_date": _to_iso(item.grenke_contract_start_date),
        "prefilled_end_date": _to_iso(item.prefilled_end_date),
        "confirmed_end_date": _to_iso(item.confirmed_end_date),
        "confirmed_at": _to_iso(item.confirmed_at),
        "confirmed_by": item.confirmed_by,
        "customer_name": item.customer_name,
        "contract_number": item.contract_number,
        "source_note": item.source_note,
        "notification_history": item.notification_history or [],
        "notification_schedule": build_contract_notification_schedule(item),
        "created_at": _to_iso(item.created_at),
        "updated_at": _to_iso(item.updated_at),
    }


async def list_delivery_cases(
    session: AsyncSession,
    *,
    source: str | None = None,
    case_type: str | None = None,
    status: str | None = None,
    include_done: bool = True,
) -> list[DeliveryCase]:
    """Zwraca listę spraw dostaw i odbiorów."""
    stmt = (
        select(DeliveryCase)
        .options(
            selectinload(DeliveryCase.devices),
            selectinload(DeliveryCase.tasks),
            selectinload(DeliveryCase.files),
            selectinload(DeliveryCase.workflow_case),
        )
        .order_by(DeliveryCase.delivery_date.asc().nulls_last(), DeliveryCase.created_at.desc())
    )
    if source:
        stmt = stmt.where(DeliveryCase.source == source)
    if case_type:
        stmt = stmt.where(DeliveryCase.case_type == case_type)
    if status:
        stmt = stmt.where(DeliveryCase.status == status)
    elif not include_done:
        stmt = stmt.where(
            DeliveryCase.status.notin_([DELIVERY_STATUS_DONE, DELIVERY_STATUS_CANCELLED])
        )
    return list((await session.execute(stmt)).scalars().all())


async def ensure_delivery_case_for_workflow(
    session: AsyncSession,
    *,
    workflow_case: FormWorkflowCase,
    form_request: FormRequest | None,
    devices: list[FormWorkflowDevice],
    updated_by: int | None,
) -> tuple[DeliveryCase, GrenkeContractEnd]:
    """Tworzy lub aktualizuje sprawę dostawy dla zatwierdzonego workflow GRENKE."""
    now = _utc_now()
    customer_name, customer_nip, customer_email, customer_phone = _company_from_workflow(
        workflow_case, form_request
    )
    stmt = select(DeliveryCase).where(DeliveryCase.workflow_case_id == workflow_case.id)
    delivery_case = (await session.execute(stmt)).scalar_one_or_none()
    if delivery_case is None:
        delivery_case = DeliveryCase(
            source=DELIVERY_SOURCE_GRENKE,
            case_type=DELIVERY_CASE_TYPE_DELIVERY,
            status=DELIVERY_STATUS_PLANNED if workflow_case.delivery_date else DELIVERY_STATUS_NEW,
            title=f"GRENKE formularz {getattr(form_request, 'id', None) or workflow_case.id}",
            form_request_id=getattr(form_request, "id", None),
            workflow_case_id=workflow_case.id,
            created_by=updated_by,
            created_at=now,
        )
        session.add(delivery_case)
    delivery_case.updated_at = now
    delivery_case.updated_by = updated_by
    delivery_case.case_type = DELIVERY_CASE_TYPE_DELIVERY
    delivery_case.firebird_client_id = workflow_case.firebird_client_id
    delivery_case.customer_name = customer_name
    delivery_case.customer_nip = customer_nip
    delivery_case.customer_email = customer_email
    delivery_case.customer_phone = customer_phone
    delivery_case.delivery_date = workflow_case.delivery_date
    delivery_case.delivery_time_window = workflow_case.delivery_time_window
    delivery_case.delivery_contact_name = workflow_case.delivery_contact_name
    delivery_case.delivery_contact_phone = workflow_case.delivery_contact_phone
    delivery_case.delivery_notes = workflow_case.delivery_notes
    delivery_case.status = (
        DELIVERY_STATUS_PLANNED if workflow_case.delivery_date else delivery_case.status
    )
    grenke_contract_start_date = resolve_workflow_grenke_contract_start_date(workflow_case)
    if workflow_case.grenke_contract_start_date is None and grenke_contract_start_date is not None:
        workflow_case.grenke_contract_start_date = grenke_contract_start_date
    delivery_case.snapshot = {
        "workflow_business_status": workflow_case.business_status,
        "grenke_contract_start_date": _to_iso(grenke_contract_start_date),
        "kp_contract_start_date": _to_iso(workflow_case.kp_contract_start_date),
        "proforma_number": workflow_case.proforma_number,
        "form_request_id": getattr(form_request, "id", None),
    }
    await session.flush()

    await session.execute(
        delete(DeliveryCaseDevice).where(DeliveryCaseDevice.delivery_case_id == delivery_case.id)
    )
    for workflow_device in devices:
        snapshot = workflow_device.snapshot if isinstance(workflow_device.snapshot, dict) else {}
        session.add(
            DeliveryCaseDevice(
                delivery_case_id=delivery_case.id,
                workflow_device_id=workflow_device.id,
                producer=workflow_device.producer,
                model=workflow_device.model,
                serial=workflow_device.serial,
                ewidencja=workflow_device.ewidencja,
                firebird_machine_id=workflow_device.firebird_machine_id,
                device_role=DELIVERY_DEVICE_ROLE_DELIVERY,
                source_type=workflow_device.source_type,
                source_row=workflow_device.source_row,
                snapshot=snapshot,
            )
        )

    contract_stmt = select(GrenkeContractEnd).where(
        GrenkeContractEnd.workflow_case_id == workflow_case.id
    )
    contract_end = (await session.execute(contract_stmt)).scalar_one_or_none()
    prefilled_date, source_note, contract_number = infer_grenke_contract_end(workflow_case)
    if contract_end is None:
        contract_end = GrenkeContractEnd(
            delivery_case_id=delivery_case.id,
            form_request_id=getattr(form_request, "id", None),
            workflow_case_id=workflow_case.id,
            status=CONTRACT_END_PENDING,
            grenke_contract_start_date=grenke_contract_start_date,
            prefilled_end_date=prefilled_date,
            customer_name=customer_name,
            contract_number=contract_number,
            source_note=source_note,
            created_at=now,
            updated_at=now,
        )
        session.add(contract_end)
    else:
        contract_end.delivery_case_id = delivery_case.id
        contract_end.form_request_id = getattr(form_request, "id", None)
        contract_end.customer_name = customer_name
        contract_end.grenke_contract_start_date = (
            contract_end.grenke_contract_start_date or grenke_contract_start_date
        )
        contract_end.updated_at = now
        if contract_end.status == CONTRACT_END_PENDING:
            contract_end.prefilled_end_date = contract_end.prefilled_end_date or prefilled_date
            contract_end.contract_number = contract_end.contract_number or contract_number
            contract_end.source_note = contract_end.source_note or source_note
    await session.flush()
    return delivery_case, contract_end


async def confirm_grenke_contract_end(
    session: AsyncSession,
    *,
    item: GrenkeContractEnd,
    confirmed_end_date: date,
    confirmed_by: int,
    contract_number: str | None = None,
) -> GrenkeContractEnd:
    """Potwierdza datę końca umowy i aktywuje wpis w kalendarzu."""
    now = _utc_now()
    item.status = CONTRACT_END_CONFIRMED
    item.confirmed_end_date = confirmed_end_date
    if item.grenke_contract_start_date is None and item.workflow_case_id is not None:
        workflow_case = await session.get(FormWorkflowCase, item.workflow_case_id)
        if workflow_case is not None:
            item.grenke_contract_start_date = resolve_workflow_grenke_contract_start_date(
                workflow_case
            )
    item.confirmed_at = now
    item.confirmed_by = confirmed_by
    item.updated_at = now
    if contract_number is not None:
        item.contract_number = _clean_text(contract_number, max_len=160)
    await session.flush()
    return item


async def list_grenke_contract_ends(
    session: AsyncSession,
    *,
    status: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    q: str | None = None,
) -> list[GrenkeContractEnd]:
    """Zwraca wpisy końców umów z filtrami do widoku powiadomień."""
    date_expr = func.coalesce(
        GrenkeContractEnd.confirmed_end_date, GrenkeContractEnd.prefilled_end_date
    )
    stmt = select(GrenkeContractEnd).order_by(
        date_expr.asc().nulls_last(),
        GrenkeContractEnd.created_at.desc(),
    )
    if status:
        stmt = stmt.where(GrenkeContractEnd.status == status)
    if date_from:
        stmt = stmt.where(date_expr >= date_from)
    if date_to:
        stmt = stmt.where(date_expr <= date_to)
    query = str(q or "").strip()
    if query:
        like = f"%{query}%"
        stmt = stmt.where(
            or_(
                GrenkeContractEnd.customer_name.ilike(like),
                GrenkeContractEnd.contract_number.ilike(like),
                cast(GrenkeContractEnd.form_request_id, Text).ilike(like),
            )
        )
    return list((await session.execute(stmt)).scalars().all())


async def load_delivery_case(session: AsyncSession, case_id: int) -> DeliveryCase | None:
    """Ładuje pełne szczegóły sprawy dostawy."""
    return (
        await session.execute(
            select(DeliveryCase)
            .options(
                selectinload(DeliveryCase.devices),
                selectinload(DeliveryCase.tasks),
                selectinload(DeliveryCase.files),
                selectinload(DeliveryCase.workflow_case),
            )
            .where(DeliveryCase.id == case_id)
        )
    ).scalar_one_or_none()


async def load_grenke_contract_end(session: AsyncSession, item_id: int) -> GrenkeContractEnd | None:
    """Ładuje wpis końca umowy GRENKE."""
    return await session.get(GrenkeContractEnd, item_id)


async def load_delivery_file(session: AsyncSession, file_id: int) -> DeliveryCaseFile | None:
    """Ładuje metadane pliku sprawy."""
    return await session.get(DeliveryCaseFile, file_id)


async def load_delivery_task(session: AsyncSession, task_id: int) -> DeliveryCaseTask | None:
    """Ładuje zadanie operacyjne sprawy."""
    return await session.get(DeliveryCaseTask, task_id)


async def list_active_device_reservations(
    session: AsyncSession,
    *,
    exclude_delivery_case_id: int | None = None,
) -> dict[str, dict[str, Any]]:
    """Zwraca aktywne rezerwacje urządzeń z formularzy i dostaw."""
    reservations: dict[str, dict[str, Any]] = {}

    workflow_stmt = (
        select(FormWorkflowDevice, FormWorkflowCase, FormRequest)
        .join(FormWorkflowCase, FormWorkflowCase.id == FormWorkflowDevice.workflow_case_id)
        .join(FormRequest, FormRequest.id == FormWorkflowCase.form_request_id)
        .where(FormRequest.status == "SUBMITTED", FormWorkflowCase.resources_released_at.is_(None))
    )
    for device, workflow_case, form_request in (await session.execute(workflow_stmt)).all():
        key = build_workflow_device_key(device.source_type, device.source_row)
        if not key:
            continue
        reservations[key] = {
            "source": "workflow",
            "label": f"Formularz {form_request.id}",
            "form_request_id": form_request.id,
            "workflow_case_id": workflow_case.id,
        }

    delivery_stmt = (
        select(DeliveryCaseDevice, DeliveryCase)
        .join(DeliveryCase, DeliveryCase.id == DeliveryCaseDevice.delivery_case_id)
        .where(DeliveryCase.status.notin_([DELIVERY_STATUS_DONE, DELIVERY_STATUS_CANCELLED]))
    )
    if exclude_delivery_case_id:
        delivery_stmt = delivery_stmt.where(DeliveryCase.id != exclude_delivery_case_id)
    for device, case in (await session.execute(delivery_stmt)).all():
        key = build_workflow_device_key(device.source_type, device.source_row)
        if not key:
            continue
        reservations[key] = {
            "source": "delivery",
            "label": f"Sprawa dostawy {case.id}",
            "delivery_case_id": case.id,
            "case_type": case.case_type,
        }
    return reservations


def build_delivery_sheet_payload(device: DeliveryCaseDevice) -> dict[str, Any]:
    """Buduje payload kompatybilny z synchronizacją arkusza urządzeń."""
    snapshot = device.snapshot if isinstance(device.snapshot, dict) else {}
    row_value = device.source_row if device.source_row is not None else snapshot.get("row")
    source_type = normalize_workflow_device_source_type(
        snapshot.get("source_type") or device.source_type,
        default="firebird_magazyn_28",
    )
    return {
        "source_row": _parse_int(row_value),
        "row": _parse_int(row_value),
        "source_type": source_type,
        "source_key": build_workflow_device_key(source_type, row_value),
        "sheet_row": _parse_int(snapshot.get("sheet_row")),
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
        "ms_id_maszyna": _parse_int(snapshot.get("ms_id_maszyna") or device.firebird_machine_id),
    }


def apply_sheet_sync_snapshot_to_delivery_devices(
    devices: list[DeliveryCaseDevice],
    *,
    operation: str,
    sheet_result: dict[str, Any] | None,
    assignee_label: str | None,
    error: str | None = None,
) -> None:
    """Zapisuje wynik synchronizacji arkusza w snapshotach urządzeń dostawy."""
    rows_by_source: dict[int, dict[str, Any]] = {}
    if isinstance(sheet_result, dict):
        for item in sheet_result.get("rows", []):
            if not isinstance(item, dict):
                continue
            source_row = _parse_int(item.get("source_row"))
            if source_row is not None:
                rows_by_source[source_row] = item

    updated_at = _utc_now().isoformat()
    for device in devices:
        snapshot = dict(device.snapshot or {})
        source_row = _parse_int(
            device.source_row if device.source_row is not None else snapshot.get("row")
        )
        row_entry = rows_by_source.get(source_row) if source_row is not None else None
        if row_entry and _parse_int(row_entry.get("sheet_row")):
            snapshot["sheet_row"] = _parse_int(row_entry.get("sheet_row"))
        previous_status = str(row_entry.get("previous_status") or "").strip() if row_entry else ""
        if previous_status:
            snapshot["sheet_previous_status"] = previous_status
        if assignee_label:
            snapshot["sheet_assignee"] = assignee_label
        snapshot["sheet_sync_updated_at"] = updated_at
        snapshot["sheet_sync_status"] = "error" if error else operation
        snapshot["sheet_sync_error"] = error
        device.snapshot = snapshot


def list_document_templates_from_disk() -> list[dict[str, Any]]:
    """Kataloguje wzory dokumentów z katalogu szablonów dostaw."""
    root = delivery_templates_root()
    if not root.exists():
        return []
    output: list[dict[str, Any]] = []
    for path in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
        if not path.is_file() or path.suffix.lower() not in _ALLOWED_TEMPLATE_SUFFIXES:
            continue
        output.append(
            {
                "template_key": path.name,
                "label": path.stem.strip() or path.name,
                "document_type": _document_type_from_name(path.name),
                "template_path": path.as_posix(),
                "file_name": path.name,
                "suffix": path.suffix.lower(),
                "docx_ready": path.suffix.lower() == ".docx",
                "source": "disk",
            }
        )
    return output


def serialize_document_template(template: DeliveryDocumentTemplate) -> dict[str, Any]:
    """Serializuje szablon zapisany w rejestrze CTIP."""
    return {
        "id": template.id,
        "template_key": template.template_key,
        "label": template.label,
        "document_type": template.document_type,
        "template_path": template.template_path,
        "active": template.active,
        "required_fields": template.required_fields or [],
        "source": "database",
    }


async def list_document_templates(session: AsyncSession) -> list[dict[str, Any]]:
    """Łączy rejestr szablonów z plikami wykrytymi w katalogu `inbox/doku`."""
    rows = list(
        (
            await session.execute(
                select(DeliveryDocumentTemplate).where(DeliveryDocumentTemplate.active.is_(True))
            )
        )
        .scalars()
        .all()
    )
    output = [serialize_document_template(row) for row in rows]
    known_keys = {item["template_key"] for item in output}
    for item in list_document_templates_from_disk():
        if item["template_key"] not in known_keys:
            output.append(item)
    return output


def resolve_document_template_path(template_key: str) -> Path:
    """Rozwiązuje nazwę szablonu do bezpiecznej ścieżki w katalogu wzorów."""
    safe_name = Path(template_key).name
    root = delivery_templates_root().resolve()
    candidate = (root / safe_name).resolve()
    if root not in candidate.parents and candidate != root:
        raise ValueError("Szablon dokumentu znajduje się poza katalogiem wzorów.")
    if not candidate.exists() or not candidate.is_file():
        raise FileNotFoundError("Szablon dokumentu nie istnieje.")
    return candidate


def build_document_context(case: DeliveryCase) -> dict[str, str]:
    """Buduje podstawowy kontekst pól dla szablonów dokumentów dostawy."""
    devices = list(getattr(case, "devices", []) or [])
    first_device = devices[0] if devices else None
    device_lines = [
        " ".join(
            part
            for part in [
                device.producer or "",
                device.model or "",
                device.serial or "",
                device.ewidencja or "",
            ]
            if part
        ).strip()
        for device in devices
    ]
    return {
        "case_id": str(case.id),
        "case_title": case.title or "",
        "case_type": case.case_type or "",
        "customer_name": case.customer_name or "",
        "customer_nip": case.customer_nip or "",
        "customer_email": case.customer_email or "",
        "customer_phone": case.customer_phone or "",
        "delivery_date": case.delivery_date.isoformat() if case.delivery_date else "",
        "delivery_time_window": case.delivery_time_window or "",
        "delivery_contact_name": case.delivery_contact_name or "",
        "delivery_contact_phone": case.delivery_contact_phone or "",
        "delivery_notes": case.delivery_notes or "",
        "service_notes": case.service_notes or "",
        "device_list": "\n".join(device_lines),
        "device_model": (
            " ".join(
                part
                for part in [
                    getattr(first_device, "producer", None),
                    getattr(first_device, "model", None),
                ]
                if part
            )
            if first_device
            else ""
        ),
        "device_serial": first_device.serial if first_device else "",
        "device_ewidencja": first_device.ewidencja if first_device else "",
        "today": _utc_now().date().isoformat(),
    }


def render_docx_template(template_path: Path, context: dict[str, str]) -> bytes:
    """Wypełnia prosty szablon DOCX z placeholderami `{{nazwa_pola}}`."""
    if template_path.suffix.lower() != ".docx":
        raise ValueError("Automatyczne wypełnianie wymaga szablonu DOCX z placeholderami.")
    from io import BytesIO

    output = BytesIO()
    with (
        zipfile.ZipFile(template_path, "r") as source,
        zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename.startswith("word/") and item.filename.endswith(".xml"):
                text = data.decode("utf-8")
                for key, value in context.items():
                    escaped = html.escape(str(value or ""), quote=False)
                    text = text.replace(f"{{{{{key}}}}}", escaped).replace(f"[[{key}]]", escaped)
                data = text.encode("utf-8")
            target.writestr(item, data)
    return output.getvalue()


async def load_notification_recipients(session: AsyncSession) -> list[AdminUser]:
    """Zwraca aktywnych handlowców do powiadomień."""
    result = await session.execute(
        select(AdminUser)
        .where(AdminUser.is_active.is_(True))
        .where(AdminUser.is_salesperson.is_(True))
        .order_by(AdminUser.id.asc())
    )
    users = list(result.scalars())
    seen: set[int] = set()
    output: list[AdminUser] = []
    for user in users:
        if user.id in seen:
            continue
        seen.add(user.id)
        output.append(user)
    return output


async def send_grenke_contract_end_reminders(session: AsyncSession) -> dict[str, int]:
    """Tworzy SMS/e-mail dla potwierdzonych końców umów w progach 60/30/7 dni."""
    today = _utc_now().date()
    items = await list_grenke_contract_ends(session, status=CONTRACT_END_CONFIRMED)
    recipients = await load_notification_recipients(session)
    email_delivery = await admin_users.resolve_email_delivery_settings(session)
    checked = 0
    eligible = 0
    sms_queued = 0
    emails_sent = 0
    skipped_no_recipients = 0
    for item in items:
        if not item.confirmed_end_date:
            continue
        days_left = (item.confirmed_end_date - today).days
        history = list(item.notification_history or [])
        end_date_key = item.confirmed_end_date.isoformat()
        sent_thresholds: set[int] = set()
        for entry in history:
            if not isinstance(entry, dict):
                continue
            key_value = str(entry.get("key") or "")
            if not key_value.endswith(f":{end_date_key}"):
                continue
            threshold_value = entry.get("threshold_days")
            if threshold_value is None:
                threshold_value = key_value.split(":", 1)[0]
            try:
                sent_thresholds.add(int(threshold_value))
            except (TypeError, ValueError):
                continue
        due_thresholds = [
            days
            for days in CONTRACT_REMINDER_THRESHOLDS
            if 0 <= days_left <= days
            and not any(sent_days <= days for sent_days in sent_thresholds)
        ]
        if not due_thresholds:
            continue
        threshold_days = min(due_thresholds, key=lambda days: days - days_left)
        checked += 1
        eligible += 1
        key = f"{threshold_days}:{item.confirmed_end_date.isoformat()}"
        if any(entry.get("key") == key for entry in history if isinstance(entry, dict)):
            continue
        subject = f"CTIP: koniec umowy GRENKE za {days_left} dni"
        text = (
            f"Umowa GRENKE klienta {item.customer_name or 'bez nazwy'} kończy się "
            f"{item.confirmed_end_date.isoformat()} ({days_left} dni)."
        )
        run_sms_queued = 0
        run_emails_sent = 0
        for recipient in recipients:
            if recipient.mobile_phone:
                session.add(
                    SmsOut(
                        dest=recipient.mobile_phone,
                        text=text[:600],
                        source="admin",
                        origin="grenke_contract_end_reminder",
                        status="NEW",
                        meta={
                            "type": "grenke_contract_end_reminder",
                            "grenke_contract_end_id": item.id,
                            "days_left": days_left,
                            "threshold_days": threshold_days,
                            "grenke_contract_start_date": (
                                item.grenke_contract_start_date.isoformat()
                                if item.grenke_contract_start_date
                                else None
                            ),
                        },
                        created_at=_utc_now().replace(tzinfo=None),
                    )
                )
                sms_queued += 1
                run_sms_queued += 1
            if email_delivery is not None and recipient.email:
                sender_name = (email_delivery.sender_name or "").strip() or "CTIP"
                sender_address = (email_delivery.sender_address or "").strip()
                if sender_address:
                    message = EmailMessage()
                    message["From"] = formataddr((sender_name, sender_address))
                    message["To"] = recipient.email
                    message["Subject"] = subject
                    message.set_content(text)
                    result = await send_smtp_message(
                        host=email_delivery.host,
                        port=email_delivery.port,
                        username=email_delivery.username,
                        password=email_delivery.password,
                        use_tls=email_delivery.use_tls,
                        use_ssl=email_delivery.use_ssl,
                        message=message,
                    )
                    if result.success:
                        emails_sent += 1
                        run_emails_sent += 1
        if run_sms_queued == 0 and run_emails_sent == 0:
            skipped_no_recipients += 1
            continue
        history.append(
            {
                "key": key,
                "days_left": days_left,
                "threshold_days": threshold_days,
                "grenke_contract_start_date": (
                    item.grenke_contract_start_date.isoformat()
                    if item.grenke_contract_start_date
                    else None
                ),
                "sent_at": _utc_now().isoformat(),
                "sms_queued": run_sms_queued,
                "emails_sent": run_emails_sent,
            }
        )
        item.notification_history = history
        item.updated_at = _utc_now()
    await session.flush()
    return {
        "checked": checked,
        "eligible": eligible,
        "sms_queued": sms_queued,
        "emails_sent": emails_sent,
        "skipped_no_recipients": skipped_no_recipients,
    }


__all__ = [
    "CONTRACT_END_CONFIRMED",
    "CONTRACT_END_PENDING",
    "CONTRACT_END_CANCELLED",
    "DELIVERY_CASE_TYPE_DELIVERY",
    "DELIVERY_CASE_TYPE_PICKUP",
    "DELIVERY_DEVICE_ROLE_DELIVERY",
    "DELIVERY_DEVICE_ROLE_PICKUP",
    "DELIVERY_SOURCE_GRENKE",
    "DELIVERY_SOURCE_MANUAL",
    "apply_sheet_sync_snapshot_to_delivery_devices",
    "build_delivery_sheet_payload",
    "build_document_context",
    "confirm_grenke_contract_end",
    "delivery_files_root",
    "ensure_delivery_case_for_workflow",
    "infer_grenke_contract_end",
    "list_active_device_reservations",
    "list_delivery_cases",
    "list_document_templates",
    "list_grenke_contract_ends",
    "load_delivery_case",
    "load_delivery_file",
    "load_delivery_task",
    "load_grenke_contract_end",
    "render_docx_template",
    "resolve_document_template_path",
    "send_grenke_contract_end_reminders",
    "serialize_delivery_case",
    "serialize_delivery_device",
    "serialize_delivery_file",
    "serialize_delivery_task",
    "serialize_grenke_contract_end",
]
