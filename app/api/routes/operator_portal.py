"""Uproszczone API wykorzystywane przez panel operatora."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from sqlalchemy import desc, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_admin_session_context, get_db_session
from app.models import Call, CallEvent, Contact, SmsOut, SmsTemplate
from app.schemas.operator import (
    OperatorCallDetail,
    OperatorCallEvent,
    OperatorCallListItem,
    OperatorContactDetail,
    OperatorContactSummary,
    OperatorContactUpsert,
    OperatorPasswordChangeRequest,
    OperatorProfile,
    OperatorProfileUpdate,
    OperatorSendSmsRequest,
    OperatorSendSmsResponse,
    OperatorSmsItem,
    OperatorSmsTemplateCreate,
    OperatorSmsTemplateRead,
    OperatorSmsTemplateUpdate,
    OperatorStats,
    OperatorUserInfo,
)
from app.services import admin_contacts, operator_settings

router = APIRouter(prefix="/operator/api", tags=["operator"])


def _ensure_operator(role: str) -> None:
    if role not in {"admin", "operator"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Brak uprawnień operatora."
        )


def _normalize_number(value: str | None) -> str | None:
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    if not digits:
        return None
    if len(digits) > 9:
        digits = digits[-9:]
    return digits


def _sms_dest_candidates(number: str | None) -> set[str]:
    """Buduje zestaw wariantów numeru do wyszukiwania historii SMS."""
    candidates: set[str] = set()
    if not number:
        return candidates

    candidates.add(number)
    digits_only = re.sub(r"\D", "", number)
    if digits_only:
        candidates.add(digits_only)
        if len(digits_only) == 9:
            candidates.add(f"+48{digits_only}")
        elif len(digits_only) > 9 and digits_only.startswith("48"):
            candidates.add(f"+{digits_only}")

    normalized = _normalize_number(number)
    if normalized:
        candidates.add(normalized)
        if len(normalized) == 9:
            candidates.add(f"+48{normalized}")

    return {candidate for candidate in candidates if candidate}


def _map_contact_summary(contact: Contact | None) -> OperatorContactSummary | None:
    if contact is None:
        return None
    return OperatorContactSummary(
        id=contact.id,
        number=contact.number,
        ext=contact.ext,
        firebird_id=contact.firebird_id,
        first_name=contact.first_name,
        last_name=contact.last_name,
        company=contact.company,
        email=contact.email,
        nip=contact.nip,
        notes=contact.notes,
    )


def _map_contact_detail(contact: Contact) -> OperatorContactDetail:
    summary = _map_contact_summary(contact)
    assert summary is not None  # zapewnia mypy, dane są kompletne
    return OperatorContactDetail(
        source=contact.source,
        created_at=contact.created_at,
        updated_at=contact.updated_at,
        **summary.model_dump(),
    )


def _map_call(call: Call, contact: Contact | None) -> OperatorCallListItem:
    return OperatorCallListItem(
        id=call.id,
        started_at=call.started_at,
        direction=call.direction,
        ext=call.ext,
        number=call.number,
        disposition=call.disposition,
        duration_s=call.duration_s,
        contact=_map_contact_summary(contact),
    )


def _map_events(events: Iterable[CallEvent]) -> list[OperatorCallEvent]:
    mapped: list[OperatorCallEvent] = []
    for event in events:
        mapped.append(
            OperatorCallEvent(
                ts=event.ts,
                typ=event.typ or "UNKNOWN",
                ext=event.ext,
                number=event.number,
                payload=event.payload,
            )
        )
    return mapped


def _sanitize_sms_text(text: str | None, origin: str | None) -> str | None:
    if not text:
        return text
    sensitive_origins = {"admin_user_credentials"}
    if origin in sensitive_origins:
        return "Treść ukryta (dane logowania administratora)."
    lowered = text.lower()
    if "hasło:" in lowered and "login:" in lowered:
        return "Treść ukryta (dane logowania administratora)."
    return text


def _map_sms(items: Iterable[SmsOut]) -> list[OperatorSmsItem]:
    history: list[OperatorSmsItem] = []
    for sms in items:
        history.append(
            OperatorSmsItem(
                id=sms.id,
                created_at=sms.created_at,
                dest=sms.dest,
                text=_sanitize_sms_text(sms.text, sms.origin),
                status=sms.status,
                origin=sms.origin,
                provider_status=sms.provider_status,
                provider_message_id=sms.provider_msg_id,
                error_msg=sms.error_msg,
            )
        )
    return history


def _map_template(template: SmsTemplate, current_user_id: int) -> OperatorSmsTemplateRead:
    return OperatorSmsTemplateRead(
        id=template.id,
        name=template.name,
        body=template.body,
        scope=template.scope,
        owner_id=template.owner_id,
        is_active=template.is_active,
        editable=template.scope == "user" and template.owner_id == current_user_id,
        created_at=template.created_at,
        updated_at=template.updated_at,
    )


@router.get("/me", response_model=OperatorUserInfo, summary="Informacje o zalogowanym operatorze")
async def operator_me(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
) -> OperatorUserInfo:
    _, admin_user = admin_context
    _ensure_operator(admin_user.role)
    return OperatorUserInfo(
        id=admin_user.id,
        email=admin_user.email,
        first_name=admin_user.first_name,
        last_name=admin_user.last_name,
        role=admin_user.role,
    )


@router.get("/calls", response_model=list[OperatorCallListItem], summary="Lista ostatnich połączeń")
async def list_operator_calls(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    limit: int = Query(default=30, ge=1, le=100),
    search: str | None = Query(default=None, min_length=1, max_length=64),
    direction: str | None = Query(default=None, pattern=r"^(IN|OUT)$"),
) -> list[OperatorCallListItem]:
    _, admin_user = admin_context
    _ensure_operator(admin_user.role)

    stmt = (
        select(Call).options(selectinload(Call.events)).order_by(desc(Call.started_at)).limit(limit)
    )

    if search:
        pattern = f"%{search.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Call.number).like(pattern),
                func.lower(Call.ext).like(pattern),
                func.lower(Call.answered_by).like(pattern),
            )
        )

    if direction:
        stmt = stmt.where(Call.direction == direction)

    result = await session.execute(stmt)
    calls = result.scalars().unique().all()

    numbers = {call.number for call in calls if call.number}
    normalized_numbers = {_normalize_number(num) for num in numbers}
    normalized_numbers.discard(None)
    lookup_candidates = {num for num in numbers if num}
    lookup_candidates.update({num for num in normalized_numbers if num})
    contacts_map: dict[str, Contact] = {}
    if lookup_candidates:
        contacts_stmt = select(Contact).where(Contact.number.in_(lookup_candidates))
        contacts_result = await session.execute(contacts_stmt)
        contacts_map = {}
        for contact in contacts_result.scalars():
            keys = {contact.number}
            normalized = _normalize_number(contact.number)
            if normalized:
                keys.add(normalized)
            for key in keys:
                contacts_map[key] = contact

    filtered: list[Call] = []
    for call in calls:
        digits = _normalize_number(call.number)
        if digits and len(digits) < 5:
            continue
        filtered.append(call)

    return [
        _map_call(call, contacts_map.get(_normalize_number(call.number) or call.number or ""))
        for call in filtered
    ]


@router.get(
    "/calls/{call_id}",
    response_model=OperatorCallDetail,
    summary="Szczegóły połączenia (oś czasu, kontakt, SMS)",
)
async def get_operator_call_detail(
    call_id: int = Path(..., ge=1),
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    sms_limit: int = Query(default=20, ge=1, le=100),
) -> OperatorCallDetail:
    _, admin_user = admin_context
    _ensure_operator(admin_user.role)

    stmt = select(Call).where(Call.id == call_id).options(selectinload(Call.events))
    result = await session.execute(stmt)
    call = result.scalars().unique().one_or_none()
    if call is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Połączenie nie istnieje."
        )

    contact: Contact | None = None
    if call.number:
        contact = await admin_contacts.fetch_contact_by_number(session, call.number)

    sms_filters = [SmsOut.call_id == call.id]
    dest_candidates = _sms_dest_candidates(call.number)
    if dest_candidates:
        sms_filters.append(SmsOut.dest.in_(dest_candidates))

    sms_stmt = select(SmsOut).order_by(desc(SmsOut.created_at)).limit(sms_limit)
    if len(sms_filters) == 1:
        sms_stmt = sms_stmt.where(sms_filters[0])
    else:
        sms_stmt = sms_stmt.where(or_(*sms_filters))

    sms_result = await session.execute(sms_stmt)
    sms_history = sms_result.scalars().all()

    return OperatorCallDetail(
        call=_map_call(call, contact),
        events=_map_events(call.events),
        sms_history=_map_sms(sms_history),
        contact=_map_contact_summary(contact),
    )


@router.get(
    "/contacts/by-number/{number}",
    response_model=OperatorContactDetail,
    summary="Szczegóły kontaktu po numerze.",
)
async def get_operator_contact(
    number: str,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> OperatorContactDetail:
    _, admin_user = admin_context
    _ensure_operator(admin_user.role)
    contact = await admin_contacts.fetch_contact_by_number(session, number)
    if contact is None:
        normalized = _normalize_number(number)
        if normalized:
            contact = await admin_contacts.fetch_contact_by_number(session, normalized)
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kontakt nie istnieje.")
    return _map_contact_detail(contact)


@router.post(
    "/contacts",
    response_model=OperatorContactDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Dodaj kontakt z panelu operatora.",
)
async def create_operator_contact(
    payload: OperatorContactUpsert,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> OperatorContactDetail:
    admin_session, admin_user = admin_context
    _ensure_operator(admin_user.role)
    normalized_number = _normalize_number(payload.number)
    if not normalized_number:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Nieprawidłowy numer telefonu."
        )
    try:
        contact = await admin_contacts.create_contact(
            session,
            number=normalized_number,
            ext=payload.ext,
            firebird_id=payload.firebird_id,
            first_name=payload.first_name,
            last_name=payload.last_name,
            company=payload.company,
            email=str(payload.email) if payload.email else None,
            nip=payload.nip,
            notes=payload.notes,
            source="operator",
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await operator_settings.record_contact_audit(
        session,
        admin_session=admin_session,
        admin_user=admin_user,
        action="contact_create_operator",
        contact=contact,
    )
    await session.commit()
    return _map_contact_detail(contact)


@router.put(
    "/contacts/{contact_id}",
    response_model=OperatorContactDetail,
    summary="Aktualizuj kontakt z panelu operatora.",
)
async def update_operator_contact(
    payload: OperatorContactUpsert,
    contact_id: int = Path(..., ge=1),
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> OperatorContactDetail:
    admin_session, admin_user = admin_context
    _ensure_operator(admin_user.role)
    contact = await admin_contacts.fetch_contact(session, contact_id)
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kontakt nie istnieje.")
    normalized_number = _normalize_number(payload.number)
    if not normalized_number:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Nieprawidłowy numer telefonu."
        )
    try:
        updated = await admin_contacts.update_contact(
            session,
            contact,
            number=normalized_number,
            ext=payload.ext,
            firebird_id=payload.firebird_id,
            first_name=payload.first_name,
            last_name=payload.last_name,
            company=payload.company,
            email=str(payload.email) if payload.email else None,
            nip=payload.nip,
            notes=payload.notes,
            source=contact.source or "operator",
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await operator_settings.record_contact_audit(
        session,
        admin_session=admin_session,
        admin_user=admin_user,
        action="contact_update_operator",
        contact=updated,
    )
    await session.commit()
    return _map_contact_detail(updated)


@router.get(
    "/sms/history",
    response_model=list[OperatorSmsItem],
    summary="Historia SMS dla wskazanego numeru.",
)
async def operator_sms_history(
    number: str = Query(..., pattern=r"^\+[1-9]\d{7,14}$"),
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    limit: int = Query(default=20, ge=1, le=200),
) -> list[OperatorSmsItem]:
    _, admin_user = admin_context
    _ensure_operator(admin_user.role)

    stmt = (
        select(SmsOut).where(SmsOut.dest == number).order_by(desc(SmsOut.created_at)).limit(limit)
    )
    result = await session.execute(stmt)
    return _map_sms(result.scalars().all())


@router.post(
    "/sms/send",
    response_model=OperatorSendSmsResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Dodaj wiadomość SMS do kolejki (panel operatora).",
)
async def operator_send_sms(
    payload: OperatorSendSmsRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> OperatorSendSmsResponse:
    _, admin_user = admin_context
    _ensure_operator(admin_user.role)

    call_id: int | None = payload.call_id
    if call_id is not None:
        call_exists = await session.scalar(select(Call.id).where(Call.id == call_id))
        if call_exists is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Powiązane połączenie nie istnieje lub zostało usunięte.",
            )

    text = payload.text.strip()
    if not text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Treść wiadomości nie może być pusta."
        )

    sms = SmsOut(
        dest=payload.dest,
        text=text,
        call_id=payload.call_id,
        origin=payload.origin or "operator",
        source="operator",
        status="NEW",
        created_by=admin_user.id,
    )
    session.add(sms)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nie udało się zapisać wiadomości – sprawdź poprawność danych.",
        ) from exc
    await session.refresh(sms)

    return OperatorSendSmsResponse(sms=_map_sms([sms])[0])


@router.get("/stats", response_model=OperatorStats, summary="Statystyki panelu operatora")
async def operator_stats(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> OperatorStats:
    _, admin_user = admin_context
    _ensure_operator(admin_user.role)

    now = datetime.now(UTC)
    try:  # pragma: no branch
        from zoneinfo import ZoneInfo
    except ImportError:  # pragma: no cover
        ZoneInfo = None

    if ZoneInfo is not None:
        local_now = now.astimezone(ZoneInfo("Europe/Warsaw"))
    else:  # pragma: no cover
        local_now = now.astimezone()
    day_start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start_local = day_start_local.replace(day=1)
    day_start = day_start_local.astimezone(UTC)
    month_start = month_start_local.astimezone(UTC)

    day_count_stmt = select(func.count(SmsOut.id)).where(SmsOut.created_at >= day_start)
    month_count_stmt = select(func.count(SmsOut.id)).where(SmsOut.created_at >= month_start)
    day_result = await session.execute(day_count_stmt)
    month_result = await session.execute(month_count_stmt)
    return OperatorStats(
        sms_today=int(day_result.scalar() or 0),
        sms_month=int(month_result.scalar() or 0),
    )


@router.get("/profile", response_model=OperatorProfile, summary="Profil operatora")
async def get_operator_profile(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
) -> OperatorProfile:
    admin_session, admin_user = admin_context
    _ensure_operator(admin_user.role)
    return OperatorProfile(
        email=admin_user.email,
        first_name=admin_user.first_name,
        last_name=admin_user.last_name,
        internal_ext=admin_user.internal_ext,
        mobile_phone=admin_user.mobile_phone,
        role=admin_user.role,
    )


@router.put("/profile", response_model=OperatorProfile, summary="Aktualizacja profilu operatora")
async def update_operator_profile(
    payload: OperatorProfileUpdate,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> OperatorProfile:
    admin_session, admin_user = admin_context
    _ensure_operator(admin_user.role)
    try:
        await operator_settings.update_profile(
            session,
            admin_user,
            email=payload.email,
            first_name=payload.first_name,
            last_name=payload.last_name,
            internal_ext=payload.internal_ext,
            mobile_phone=payload.mobile_phone,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await session.commit()
    return OperatorProfile(
        email=admin_user.email,
        first_name=admin_user.first_name,
        last_name=admin_user.last_name,
        internal_ext=admin_user.internal_ext,
        mobile_phone=admin_user.mobile_phone,
        role=admin_user.role,
    )


@router.post(
    "/profile/change-password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Zmiana hasła operatora",
    response_class=Response,
)
async def change_operator_password(
    payload: OperatorPasswordChangeRequest,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> Response:
    admin_session, admin_user = admin_context
    _ensure_operator(admin_user.role)
    try:
        await operator_settings.change_password(
            session,
            admin_user,
            current_password=payload.current_password,
            new_password=payload.new_password,
            exclude_token=admin_session.token,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/sms/templates",
    response_model=list[OperatorSmsTemplateRead],
    summary="Lista szablonów SMS operatora",
)
async def list_operator_templates(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> list[OperatorSmsTemplateRead]:
    _, admin_user = admin_context
    _ensure_operator(admin_user.role)
    templates = await operator_settings.list_templates(session, admin_user.id)
    return [_map_template(template, admin_user.id) for template in templates]


@router.post(
    "/sms/templates",
    response_model=OperatorSmsTemplateRead,
    status_code=status.HTTP_201_CREATED,
    summary="Dodaj szablon SMS operatora",
)
async def create_operator_template(
    payload: OperatorSmsTemplateCreate,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> OperatorSmsTemplateRead:
    _, admin_user = admin_context
    _ensure_operator(admin_user.role)
    template = await operator_settings.create_template(
        session,
        user_id=admin_user.id,
        name=payload.name,
        body=payload.body,
        is_active=payload.is_active,
    )
    await session.commit()
    await session.refresh(template)
    return _map_template(template, admin_user.id)


@router.put(
    "/sms/templates/{template_id}",
    response_model=OperatorSmsTemplateRead,
    summary="Aktualizuj szablon SMS operatora",
)
async def update_operator_template(
    payload: OperatorSmsTemplateUpdate,
    template_id: int = Path(..., ge=1),
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> OperatorSmsTemplateRead:
    _, admin_user = admin_context
    _ensure_operator(admin_user.role)
    template = await operator_settings.fetch_template(session, template_id)
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Szablon nie istnieje.")
    try:
        updated = await operator_settings.update_template(
            session,
            template,
            user_id=admin_user.id,
            name=payload.name,
            body=payload.body,
            is_active=payload.is_active,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    await session.commit()
    await session.refresh(updated)
    return _map_template(updated, admin_user.id)


@router.delete(
    "/sms/templates/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Usuń szablon SMS operatora",
    response_class=Response,
)
async def delete_operator_template(
    template_id: int = Path(..., ge=1),
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> Response:
    _, admin_user = admin_context
    _ensure_operator(admin_user.role)
    template = await operator_settings.fetch_template(session, template_id)
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Szablon nie istnieje.")
    try:
        await operator_settings.delete_template(session, template, user_id=admin_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
