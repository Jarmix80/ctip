"""API generatora bezpiecznych formularzy."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_session_context, get_db_session
from app.models import AdminUser, FormRequest
from app.schemas.form_generator import (
    FormRequestCreate,
    FormRequestCreateResponse,
    FormRequestDetailResponse,
    FormRequestListResponse,
    FormRequestSummary,
)
from app.services import form_generator, section_permissions
from app.services.audit import record_audit
from app.services.contracts_dashboard import (
    load_firebird_runtime_config,
    use_firebird_runtime_config,
)
from app.services.contracts_proforma import delete_proforma_from_firebird
from app.services.contracts_workflow import get_form_workflow_case, list_form_workflow_devices
from app.services.workflow_sheet_sync import (
    load_workflow_sheet_runtime_config,
    release_workflow_devices_from_sheet,
    use_workflow_sheet_runtime_config,
)

router = APIRouter(prefix="/admin/forms", tags=["admin-forms"])


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
