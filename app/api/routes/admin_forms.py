"""API generatora bezpiecznych formularzy."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_session_context, get_db_session
from app.models import AdminAuditLog, AdminUser, FormRequest
from app.schemas.form_generator import (
    FormRequestCreate,
    FormRequestCreateResponse,
    FormRequestDataEnteredEmailStatus,
    FormRequestDetailResponse,
    FormRequestListResponse,
    FormRequestNotifyDataEnteredResponse,
    FormRequestSummary,
)
from app.services import admin_users, form_generator, section_permissions
from app.services.audit import record_audit
from app.services.contracts_dashboard import (
    load_firebird_runtime_config,
    use_firebird_runtime_config,
)
from app.services.contracts_proforma import delete_proforma_from_firebird
from app.services.contracts_workflow import get_form_workflow_case, list_form_workflow_devices
from app.services.email_client import send_smtp_message
from app.services.workflow_sheet_sync import (
    load_workflow_sheet_runtime_config,
    release_workflow_devices_from_sheet,
    use_workflow_sheet_runtime_config,
)

router = APIRouter(prefix="/admin/forms", tags=["admin-forms"])

FORM_DATA_ENTERED_EMAIL_AUDIT_ACTION = "form_request_data_entered_email_sent"
FORM_DATA_ENTERED_EMAIL_SUBJECT = "Informacja o dalszych krokach umowy najmu"


async def _ensure_admin_or_operator(
    session: AsyncSession,
    user: AdminUser,
) -> None:
    if user.role not in {"admin", "operator"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operacja wymaga roli administratora lub operatora.",
        )
    if not await section_permissions.user_has_section(session, user, "generator"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Konto nie ma uprawnień do sekcji generatora formularzy.",
        )


def _resolve_creator_name(item: FormRequest, fallback_user: AdminUser | None = None) -> str | None:
    creator = getattr(item, "created_by_user", None)
    if creator is None and fallback_user is not None and item.created_by == fallback_user.id:
        creator = fallback_user
    if creator is None:
        if item.created_by is not None:
            return f"ID {item.created_by}"
        return None
    full_name = " ".join([part for part in [creator.first_name, creator.last_name] if part]).strip()
    if full_name:
        return full_name
    return creator.email


def _to_summary(item: FormRequest, *, fallback_user: AdminUser | None = None) -> FormRequestSummary:
    return FormRequestSummary(
        id=item.id,
        customer_name=item.customer_name,
        customer_email=item.customer_email,
        customer_phone=item.customer_phone,
        created_by_name=_resolve_creator_name(item, fallback_user=fallback_user),
        status=item.status,
        token_expires_at=item.token_expires_at,
        submitted_at=item.submitted_at,
        created_at=item.created_at,
        sms_status=item.sms_status,
        email_status=item.email_status,
        ms_status=item.ms_status,
    )


def _coerce_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _build_sheet_release_payloads(workflow_devices: list[Any]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for device in workflow_devices:
        snapshot = device.snapshot if isinstance(device.snapshot, dict) else {}
        payloads.append(
            {
                "source_row": _coerce_int(
                    device.source_row if device.source_row is not None else snapshot.get("row")
                ),
                "row": _coerce_int(
                    device.source_row if device.source_row is not None else snapshot.get("row")
                ),
                "sheet_row": _coerce_int(snapshot.get("sheet_row")),
                "producer": str(snapshot.get("producer") or device.producer or "").strip(),
                "model": str(snapshot.get("model") or device.model or "").strip(),
                "serial": str(snapshot.get("serial") or device.serial or "").strip(),
                "ewidencja": str(snapshot.get("ewidencja") or device.ewidencja or "").strip(),
                "index": str(
                    snapshot.get("index") or snapshot.get("ewidencja") or device.ewidencja or ""
                ).strip(),
            }
        )
    return payloads


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _build_salesperson_signature(user: AdminUser) -> tuple[str, str, str]:
    salesperson_name = " ".join(
        [
            part.strip()
            for part in [user.first_name or "", user.last_name or ""]
            if part and part.strip()
        ]
    ).strip()
    return (
        salesperson_name or user.email or "Opiekun handlowy",
        (user.email or "").strip() or "brak e-mail",
        (user.mobile_phone or "").strip() or "brak numeru",
    )


def _resolve_client_recipient_email(
    item: FormRequest, submitted_payload: dict[str, Any]
) -> str | None:
    company_email = str(submitted_payload.get("company_email") or "").strip().lower()
    if company_email:
        return company_email
    fallback = (item.customer_email or "").strip().lower()
    return fallback or None


def _build_data_entered_email_body(
    *,
    item: FormRequest,
    submitted_payload: dict[str, Any],
    salesperson: AdminUser,
) -> str:
    company_name = str(submitted_payload.get("company_name") or item.customer_name or "").strip()
    company_nip = str(submitted_payload.get("company_nip") or "").strip()
    representatives = submitted_payload.get("representatives")
    representative_line = ""
    if isinstance(representatives, list):
        names: list[str] = []
        for representative in representatives:
            if not isinstance(representative, dict):
                continue
            first_name = str(representative.get("first_name") or "").strip()
            last_name = str(representative.get("last_name") or "").strip()
            full_name = " ".join([part for part in [first_name, last_name] if part]).strip()
            if full_name:
                names.append(full_name)
        if names:
            representative_line = f"Osoby reprezentujące: {', '.join(names)}."

    salesperson_name, salesperson_email, salesperson_mobile = _build_salesperson_signature(
        salesperson
    )
    company_identity = company_name or item.customer_name
    company_line = f"Dane formularza: {company_identity}"
    if company_nip:
        company_line += f", NIP: {company_nip}"

    lines = [
        f"Dzień dobry{f' {company_identity},' if company_identity else ','}",
        "dziękujemy za wybór naszej oferty wynajmu kserokopiarki.",
        company_line + ".",
    ]
    if representative_line:
        lines.append(representative_line)
    lines.extend(
        [
            "",
            "W najbliższym czasie otrzymają Państwo wiadomość e-mail z umową najmu, która zostanie przesłana przez naszego partnera – firmę Grenke.",
            "Podpisanie umowy odbywa się w wygodnej i w pełni bezpiecznej formie elektronicznej, za pośrednictwem systemu Autenti.",
            "Po podpisaniu dokumentu oraz zakończeniu procesu weryfikacji w systemie Autenti umowa zostanie aktywowana.",
            "W wiadomości znajduje się również link aktywacyjny do dokonania płatności weryfikacyjnej w wysokości 0,01 zł (kwota jest automatycznie zwracana na konto).",
            "Równolegle pozostajemy w kontakcie w celu ustalenia dogodnego terminu dostawy oraz instalacji urządzenia.",
            "Na tym etapie dysponujemy już wszystkimi niezbędnymi elementami do przeprowadzenia instalacji.",
            "W razie jakichkolwiek pytań pozostajemy do Państwa dyspozycji.",
            "",
            "Dobrego dnia",
            salesperson_name,
            salesperson_email,
            salesperson_mobile,
            "",
            "Ksero Partner Mikołaj Frąszczak Spółka Komandytowa",
            "ul. Fabianowska 165, 62-052 Komorniki",
        ]
    )
    return "\n".join(lines)


async def _load_data_entered_email_status(
    session: AsyncSession,
    *,
    form_request_id: int,
) -> FormRequestDataEnteredEmailStatus:
    stmt = (
        select(AdminAuditLog)
        .where(AdminAuditLog.action == FORM_DATA_ENTERED_EMAIL_AUDIT_ACTION)
        .order_by(AdminAuditLog.created_at.desc(), AdminAuditLog.id.desc())
    )
    entries = (await session.execute(stmt)).scalars().all()
    for entry in entries:
        payload = entry.payload if isinstance(entry.payload, dict) else {}
        if _coerce_int(payload.get("form_request_id")) != form_request_id:
            continue
        sent_at = _parse_iso_datetime(payload.get("sent_at")) or entry.created_at
        recipient_email = str(payload.get("recipient_email") or "").strip().lower() or None
        return FormRequestDataEnteredEmailStatus(
            sent=True,
            sent_at=sent_at,
            recipient_email=recipient_email,
        )
    return FormRequestDataEnteredEmailStatus(sent=False)


@router.get("", response_model=FormRequestListResponse, summary="Lista wygenerowanych formularzy")
async def list_forms(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> FormRequestListResponse:
    """Zwraca listę wygenerowanych formularzy handlowych."""
    _, admin_user = admin_context
    await _ensure_admin_or_operator(session, admin_user)
    items = await form_generator.list_form_requests(session, limit=300)
    await session.commit()
    return FormRequestListResponse(items=[_to_summary(item) for item in items])


@router.get(
    "/{form_id}",
    response_model=FormRequestDetailResponse,
    summary="Szczegóły formularza",
)
async def get_form_detail(
    form_id: int = Path(..., ge=1),
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> FormRequestDetailResponse:
    """Zwraca szczegóły formularza oraz zapisane dane klienta (jeśli wysłane)."""
    admin_session, admin_user = admin_context
    await _ensure_admin_or_operator(session, admin_user)

    item = await form_generator.get_form_request_by_id(session, form_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Formularz nie istnieje.")

    submitted_payload = None
    submitted_meta = None
    if item.status == "SUBMITTED":
        try:
            submitted_payload, submitted_meta = form_generator.decode_submitted_payload(item)
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
            ) from exc
        data_entered_email_status = await _load_data_entered_email_status(
            session,
            form_request_id=item.id,
        )
    else:
        data_entered_email_status = FormRequestDataEnteredEmailStatus(sent=False)

    await record_audit(
        session,
        user_id=admin_user.id,
        action="form_request_view",
        client_ip=admin_session.client_ip,
        payload={"form_request_id": item.id, "status": item.status},
    )
    await session.commit()
    return FormRequestDetailResponse(
        item=_to_summary(item),
        status_message=form_generator.build_status_message(item),
        submitted_payload=submitted_payload,
        submitted_meta=submitted_meta,
        data_entered_email=data_entered_email_status,
    )


@router.post(
    "/{form_id}/notify-data-entered",
    response_model=FormRequestNotifyDataEnteredResponse,
    summary="Wyślij e-mail po potwierdzeniu wpisania danych klienta",
)
async def notify_data_entered(
    form_id: int = Path(..., ge=1),
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> FormRequestNotifyDataEnteredResponse:
    """Wysyła klientowi informację o dalszym przebiegu procesu po złożeniu formularza."""
    admin_session, admin_user = admin_context
    await _ensure_admin_or_operator(session, admin_user)

    item = await form_generator.get_form_request_by_id(session, form_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Formularz nie istnieje.")
    if item.status != "SUBMITTED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Powiadomienie można wysłać dopiero po wypełnieniu formularza przez klienta.",
        )

    try:
        submitted_payload, _ = form_generator.decode_submitted_payload(item)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    if not isinstance(submitted_payload, dict):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Brak zapisanych danych formularza klienta.",
        )

    recipient_email = _resolve_client_recipient_email(item, submitted_payload)
    if not recipient_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Brak adresu e-mail klienta do wysyłki powiadomienia.",
        )

    existing_status = await _load_data_entered_email_status(session, form_request_id=item.id)
    if existing_status.sent:
        sent_at_text = (
            existing_status.sent_at.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
            if existing_status.sent_at
            else "nieznany termin"
        )
        existing_recipient = existing_status.recipient_email or recipient_email
        return FormRequestNotifyDataEnteredResponse(
            ok=True,
            already_sent=True,
            message=(
                "Wiadomość została już wcześniej wysłana "
                f"({sent_at_text}) na adres {existing_recipient}."
            ),
            recipient_email=existing_recipient,
            sent_at=existing_status.sent_at,
        )

    email_delivery = await admin_users.resolve_email_delivery_settings(session)
    if email_delivery is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Brak konfiguracji SMTP. Nie można wysłać powiadomienia e-mail.",
        )
    if not email_delivery.sender_address:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Konfiguracja SMTP nie zawiera adresu nadawcy.",
        )

    message = EmailMessage()
    sender_name = (email_delivery.sender_name or "").strip() or "CTIP Administrator"
    message["From"] = formataddr((sender_name, email_delivery.sender_address))
    message["To"] = recipient_email
    message["Subject"] = FORM_DATA_ENTERED_EMAIL_SUBJECT
    message.set_content(
        _build_data_entered_email_body(
            item=item,
            submitted_payload=submitted_payload,
            salesperson=admin_user,
        )
    )
    send_result = await send_smtp_message(
        host=email_delivery.host,
        port=email_delivery.port,
        username=email_delivery.username,
        password=email_delivery.password,
        use_tls=email_delivery.use_tls,
        use_ssl=email_delivery.use_ssl,
        message=message,
    )
    if not send_result.success:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=send_result.message or "Wysyłka e-mail zakończyła się błędem.",
        )

    sent_at = datetime.now(UTC)
    await record_audit(
        session,
        user_id=admin_user.id,
        action=FORM_DATA_ENTERED_EMAIL_AUDIT_ACTION,
        client_ip=admin_session.client_ip,
        payload={
            "form_request_id": item.id,
            "recipient_email": recipient_email,
            "sent_at": sent_at.isoformat(),
            "customer_name": item.customer_name,
            "customer_email": item.customer_email,
            "triggered_by_user_id": admin_user.id,
        },
    )
    await session.commit()

    return FormRequestNotifyDataEnteredResponse(
        ok=True,
        already_sent=False,
        message=f"Wiadomość została wysłana na adres {recipient_email}.",
        recipient_email=recipient_email,
        sent_at=sent_at,
    )


@router.post(
    "",
    response_model=FormRequestCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Wygeneruj nowy formularz",
)
async def create_form(
    payload: FormRequestCreate,
    request: Request,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> FormRequestCreateResponse:
    """Tworzy jednorazowy link formularza i uruchamia dystrybucję SMS/e-mail."""
    admin_session, admin_user = admin_context
    await _ensure_admin_or_operator(session, admin_user)

    try:
        item, form_url, notifications = await form_generator.create_form_request(
            session,
            created_by=admin_user.id,
            customer_name=payload.customer_name,
            customer_email=payload.customer_email,
            customer_phone=payload.customer_phone,
            expires_on=payload.expires_on,
            request_base_url=str(request.base_url),
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await record_audit(
        session,
        user_id=admin_user.id,
        action="form_request_create",
        client_ip=admin_session.client_ip,
        payload={
            "form_request_id": item.id,
            "customer_name": item.customer_name,
            "customer_email": item.customer_email,
            "customer_phone": item.customer_phone,
            "sms_queued": notifications.sms_queued,
            "email_sent": notifications.email_sent,
            "warnings": notifications.warnings,
        },
    )
    await session.commit()

    return FormRequestCreateResponse(
        item=_to_summary(item, fallback_user=admin_user),
        form_url=form_url,
        sms_queued=notifications.sms_queued,
        email_sent=notifications.email_sent,
        warnings=notifications.warnings,
    )


@router.delete(
    "/{form_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Usuń formularz",
    response_class=Response,
)
async def delete_form(
    form_id: int = Path(..., ge=1),
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> Response:
    """Usuwa wybrany wpis formularza z listy generatora."""
    admin_session, admin_user = admin_context
    await _ensure_admin_or_operator(session, admin_user)

    item = await form_generator.get_form_request_by_id(session, form_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Formularz nie istnieje.")

    sheet_release_result: dict[str, Any] | None = None
    firebird_delete_result: dict[str, Any] | None = None
    workflow_case = await get_form_workflow_case(session, form_request_id=item.id)
    if workflow_case is not None:
        workflow_devices = await list_form_workflow_devices(
            session, workflow_case_id=workflow_case.id
        )
        if workflow_devices:
            sheet_payloads = _build_sheet_release_payloads(workflow_devices)
            if sheet_payloads:
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
                        detail=(
                            "Nie udalo sie zwolnic rezerwacji arkusza dla usuwanego formularza: "
                            f"{exc}"
                        ),
                    ) from exc
        if workflow_case.proforma_firebird_id:
            firebird_config = await load_firebird_runtime_config(session)
            try:
                with use_firebird_runtime_config(firebird_config):
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
                    detail=(
                        "Nie udalo sie usunac proformy Firebird dla dezaktywowanego formularza: "
                        f"{exc}"
                    ),
                ) from exc
            if not delete_result.deleted:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Nie znaleziono proformy w aktywnej bazie Firebird dla dezaktywowanego formularza. "
                        "Dezaktywacja zostala przerwana."
                    ),
                )
            firebird_delete_result = {
                "deleted": bool(delete_result.deleted),
                "deleted_lines": int(delete_result.deleted_lines or 0),
                "pdf_deleted": bool(delete_result.pdf_deleted),
                "proforma_firebird_id": int(delete_result.id_faktura_table),
            }

    await form_generator.delete_form_request(session, item)
    await record_audit(
        session,
        user_id=admin_user.id,
        action="form_request_delete",
        client_ip=admin_session.client_ip,
        payload={
            "deleted_form_request_id": form_id,
            "customer_email": item.customer_email,
            "status": item.status,
            "sheet_release_enabled": bool(
                sheet_release_result and sheet_release_result.get("enabled")
            ),
            "sheet_release_count": (
                int(sheet_release_result.get("released_count") or 0) if sheet_release_result else 0
            ),
            "sheet_release_reason": (
                sheet_release_result.get("reason") if sheet_release_result else None
            ),
            "firebird_delete": firebird_delete_result,
        },
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
