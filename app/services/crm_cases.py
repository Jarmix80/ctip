"""Logika trwałych spraw Centrum Obsługi bez bezpośrednich wysyłek i zapisów Firebird."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models import AdminUser, CrmCase, CrmCaseEvent
from app.schemas.crm import (
    ChatCaseCreateRequest,
    ChatCaseResponse,
    CrmCaseActionRequest,
    CrmCaseCreateRequest,
    CrmCaseEventResponse,
    CrmCaseResponse,
    CrmOperatorResponse,
)
from app.services.audit import record_audit
from app.services.bot_identity_directory import validate_chat_device_selection

QUEUE_ALIASES = {
    "sales": "sales",
    "handel": "sales",
    "service": "service_it",
    "service_it": "service_it",
    "serwis": "service_it",
    "it": "service_it",
    "accounting": "other",
    "ksiegowosc": "other",
    "contracts": "contracts",
    "contracts_settlements": "contracts",
    "umowy": "contracts",
    "meters": "contracts",
    "liczniki": "contracts",
    "other": "other",
    "inne": "other",
}
CHAT_CATEGORY_BY_QUEUE = {
    "sales": "sales",
    "service_it": "service",
    "contracts": "contracts_settlements",
    "other": "other",
}
CRM_QUEUE_BY_CHAT_CATEGORY = {
    "sales": "sales",
    "service": "service_it",
    "accounting": "other",
    "contracts_settlements": "contracts",
    "other": "other",
}
CHAT_STATUS_BY_CRM_STATUS = {
    "new": "queued",
    "active": "in_progress",
    "transferred": "accepted",
    "done": "resolved",
    "archived": "resolved",
}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _case_ref(*, is_lab: bool) -> str:
    prefix = "KP-LAB" if is_lab else "KP"
    return f"{prefix}-{_utc_now():%Y%m%d}-{secrets.token_hex(4).upper()}"


def _idempotency_hash(channel: str, value: str | None) -> str | None:
    if not value:
        return None
    raw = f"{channel}:{value.strip()}".encode()
    return hashlib.sha256(raw).hexdigest()


def _user_name(user: AdminUser) -> str:
    full_name = " ".join(part for part in (user.first_name, user.last_name) if part)
    return full_name or user.email


def _queue(payload: CrmCaseCreateRequest) -> str:
    raw = payload.queue or payload.category or "other"
    return QUEUE_ALIASES.get(str(raw).strip().lower(), "other")


def _event(
    *,
    event_type: str,
    title: str,
    description: str | None,
    actor: AdminUser | None,
    payload: dict | None = None,
) -> CrmCaseEvent:
    return CrmCaseEvent(
        event_type=event_type,
        title=title,
        description=description,
        actor_user_id=actor.id if actor else None,
        actor_name=_user_name(actor) if actor else None,
        payload=payload,
    )


def _serialize_event(item: CrmCaseEvent) -> CrmCaseEventResponse:
    return CrmCaseEventResponse(
        ref=item.ref,
        type=item.event_type,
        title=item.title,
        text=item.description,
        actor=item.actor_name,
        payload=item.payload,
        created_at=item.created_at,
    )


def serialize_case(item: CrmCase) -> CrmCaseResponse:
    """Buduje bezpieczną odpowiedź sprawy wraz z uporządkowaną osią zdarzeń."""
    return CrmCaseResponse(
        ref=item.ref,
        external_ref=item.external_ref,
        conversation_ref=item.conversation_ref,
        source=item.source_channel,  # type: ignore[arg-type]
        source_detail=item.source_detail,
        source_url=item.source_url,
        queue=item.queue,  # type: ignore[arg-type]
        category=item.category,
        status=item.status,  # type: ignore[arg-type]
        priority=item.priority,  # type: ignore[arg-type]
        subject=item.subject,
        message=item.message,
        company_name=item.company_name,
        contact_name=item.contact_name,
        contact_phone=item.contact_phone,
        contact_email=item.contact_email,
        customer_ref=item.customer_ref,
        identity_status=item.identity_status,
        device_label=item.device_label,
        device_refs=item.device_refs,
        device_serial_last4=item.device_serial_last4,
        owner_user_id=item.owner_user_id,
        owner_name=item.owner_name,
        declared_operator_id=item.declared_operator_id,
        ms_order_ref=item.ms_order_ref,
        is_lab=item.is_lab,
        created_at=item.created_at,
        updated_at=item.updated_at,
        first_claimed_at=item.first_claimed_at,
        terminal_at=item.terminal_at,
        archived_at=item.archived_at,
        retained_until=item.retained_until,
        events=[_serialize_event(event) for event in item.events],
    )


def serialize_chat_case(item: CrmCase) -> ChatCaseResponse:
    """Mapuje wewnętrzną sprawę Centrum Obsługi na kontrakt CHAT_KP."""
    category = item.category.strip().lower()
    if category not in {
        "sales",
        "service",
        "accounting",
        "other",
        "contracts_settlements",
    }:
        category = CHAT_CATEGORY_BY_QUEUE.get(item.queue, "other")
    return ChatCaseResponse(
        case_id=item.ref,
        external_reference=item.external_ref,
        status=CHAT_STATUS_BY_CRM_STATUS.get(item.status, "failed"),  # type: ignore[arg-type]
        category=category,  # type: ignore[arg-type]
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


async def create_chat_case(
    session: AsyncSession,
    payload: ChatCaseCreateRequest,
    *,
    idempotency_key: str | None,
    service_channel: str,
) -> CrmCase:
    """Zapisuje sprawę CHAT_KP bez utrwalania NIP-u i pełnych danych katalogu."""
    selected_device_refs = payload.selected_device_refs()
    if selected_device_refs:
        if service_channel != "chat":
            raise ValueError("Wybór urządzeń jest dostępny wyłącznie dla kanału chat.")
        if not payload.customer_ref or not payload.sms_challenge_id:
            raise ValueError(
                "Wybór urządzeń wymaga klienta i poprawnie zweryfikowanego wyzwania SMS."
            )
        await validate_chat_device_selection(
            session,
            customer_ref=payload.customer_ref,
            challenge_id=payload.sms_challenge_id,
            device_refs=selected_device_refs,
        )
    customer_label = (payload.customer_name or "Klient CHAT_KP").strip()
    crm_payload = CrmCaseCreateRequest(
        external_ref=payload.conversation_ref,
        conversation_ref=payload.conversation_ref,
        channel="chat",
        queue=CRM_QUEUE_BY_CHAT_CATEGORY[payload.category],  # type: ignore[arg-type]
        category=payload.category,
        subject=payload.summary,
        message=payload.description or payload.summary,
        company_name=customer_label,
        customer_ref=payload.customer_ref,
        contact_name=customer_label,
        phone=payload.contact_phone,
        identity_status=payload.customer_match_status,
        device_label=selected_device_refs[0] if selected_device_refs else None,
        device_refs=selected_device_refs or None,
        is_lab=settings.crm_lab_mode,
        source_detail="CHAT_KP",
        metadata={
            "source_system": payload.source_system,
            "sms_challenge_id": payload.sms_challenge_id,
            "phone_verification_status": payload.phone_verification_status,
            "privacy_notice_version": payload.privacy_notice_version,
            "privacy_notice_checksum": payload.privacy_notice_checksum,
            "customer_confirmed": payload.customer_confirmed,
            "privacy_notice_accepted": payload.privacy_notice_accepted,
        },
    )
    item, _ = await create_case(
        session,
        crm_payload,
        idempotency_key=idempotency_key,
        service_channel=service_channel,
        declared_operator=None,
        force_lab=settings.crm_lab_mode,
    )
    return item


async def get_active_operator(session: AsyncSession, user_id: int) -> AdminUser:
    """Zwraca aktywnego użytkownika, którego można wskazać jako operatora LAB."""
    user = await session.scalar(
        select(AdminUser).where(
            AdminUser.id == user_id,
            AdminUser.is_active.is_(True),
            AdminUser.role.in_({"admin", "operator"}),
        )
    )
    if user is None:
        raise ValueError("Wybrany operator jest nieaktywny albo nie istnieje.")
    return user


async def list_active_operators(session: AsyncSession) -> list[CrmOperatorResponse]:
    """Zwraca aktywnych użytkowników dostępnych w selektorze operatora."""
    users = (
        await session.scalars(
            select(AdminUser)
            .where(
                AdminUser.is_active.is_(True),
                AdminUser.role.in_({"admin", "operator"}),
            )
            .order_by(AdminUser.first_name, AdminUser.last_name, AdminUser.email)
        )
    ).all()
    return [
        CrmOperatorResponse(
            id=user.id,
            name=_user_name(user),
            email=user.email,
            role=user.role,
            phone_available=bool(user.mobile_phone),
        )
        for user in users
    ]


async def create_case(
    session: AsyncSession,
    payload: CrmCaseCreateRequest,
    *,
    idempotency_key: str | None,
    service_channel: str | None,
    declared_operator: AdminUser | None,
    force_lab: bool,
) -> tuple[CrmCase, bool]:
    """Tworzy sprawę idempotentnie albo zwraca istniejący rekord."""
    if service_channel == "voice" and payload.channel not in {"voice", "phone", "scenario"}:
        raise ValueError("Token voice nie może tworzyć spraw kanału webowego.")
    if service_channel == "chat" and payload.channel in {"voice", "phone"}:
        raise ValueError("Token chat nie może tworzyć spraw kanału głosowego.")
    if service_channel == "www" and payload.channel not in {"form", "configurator"}:
        raise ValueError("Token WWW może tworzyć wyłącznie sprawy formularzowe.")

    external_ref = (
        payload.external_ref or payload.case_ref or payload.conversation_ref or ""
    ).strip()
    key_hash = _idempotency_hash(service_channel or payload.channel, idempotency_key)
    existing = None
    if key_hash:
        existing = await session.scalar(
            select(CrmCase)
            .options(selectinload(CrmCase.events))
            .where(CrmCase.idempotency_key_hash == key_hash)
        )
    if existing is None:
        existing = await session.scalar(
            select(CrmCase)
            .options(selectinload(CrmCase.events))
            .where(
                CrmCase.source_channel == payload.channel,
                CrmCase.external_ref == external_ref,
            )
        )
    if existing is not None:
        return existing, False

    company_name = payload.company.name if payload.company else payload.company_name or ""
    customer_ref = payload.company.customer_ref if payload.company else payload.customer_ref
    contact_name = payload.contact.name if payload.contact else payload.contact_name or ""
    contact_phone = payload.contact.phone if payload.contact else payload.phone
    contact_email = (
        str(payload.contact.email) if payload.contact and payload.contact.email else None
    ) or (str(payload.email) if payload.email else None)
    message = payload.message or payload.content or payload.summary or ""
    is_lab = bool(force_lab or payload.is_lab)
    now = _utc_now()
    queue = _queue(payload)
    item = CrmCase(
        ref=_case_ref(is_lab=is_lab),
        external_ref=external_ref,
        conversation_ref=payload.conversation_ref,
        source_channel=payload.channel,
        source_detail=payload.source_detail,
        source_url=payload.source_url,
        queue=queue,
        category=(payload.category or queue).strip().lower(),
        status="new",
        priority=payload.priority,
        subject=payload.subject.strip(),
        message=message.strip(),
        company_name=company_name.strip(),
        contact_name=contact_name.strip(),
        contact_phone=contact_phone.strip() if contact_phone else None,
        contact_email=contact_email,
        customer_ref=customer_ref,
        identity_status=payload.identity_status,
        device_label=payload.device_label,
        device_refs=payload.device_refs,
        device_serial_last4=payload.device_serial_last4,
        declared_operator_id=declared_operator.id if declared_operator else None,
        idempotency_key_hash=key_hash,
        source_payload=payload.metadata or None,
        is_lab=is_lab,
        created_at=now,
        updated_at=now,
        retained_until=now + timedelta(days=max(1, settings.crm_retention_days)),
    )
    item.events.append(
        _event(
            event_type=payload.channel,
            title="Przyjęcie sprawy",
            description=f"Sprawa wpłynęła kanałem {payload.channel}.",
            actor=declared_operator,
            payload={"queue": item.queue, "is_lab": item.is_lab},
        )
    )
    session.add(item)
    await session.flush()
    return item, True


async def get_case(session: AsyncSession, case_ref: str) -> CrmCase | None:
    """Pobiera sprawę wraz z osią zdarzeń."""
    return await session.scalar(
        select(CrmCase).options(selectinload(CrmCase.events)).where(CrmCase.ref == case_ref)
    )


async def archive_due_cases(session: AsyncSession) -> int:
    """Archiwizuje sprawy po ustalonym okresie od zakończenia."""
    now = _utc_now()
    threshold = now - timedelta(days=max(1, settings.crm_auto_archive_days))
    result = await session.execute(
        update(CrmCase)
        .where(
            CrmCase.status.in_({"done", "transferred"}),
            CrmCase.terminal_at.is_not(None),
            CrmCase.terminal_at <= threshold,
        )
        .values(status="archived", archived_at=now, updated_at=now)
    )
    return int(result.rowcount or 0)


async def list_cases(
    session: AsyncSession,
    *,
    include_archived: bool = True,
    lab_only: bool | None = None,
    limit: int = 500,
) -> list[CrmCase]:
    """Zwraca najnowsze sprawy dla interfejsu operatora."""
    await archive_due_cases(session)
    statement = select(CrmCase).options(selectinload(CrmCase.events))
    if not include_archived:
        statement = statement.where(CrmCase.status != "archived")
    if lab_only is not None:
        statement = statement.where(CrmCase.is_lab.is_(lab_only))
    statement = statement.order_by(CrmCase.updated_at.desc()).limit(min(max(limit, 1), 1000))
    return list((await session.scalars(statement)).all())


async def apply_case_action(
    session: AsyncSession,
    item: CrmCase,
    payload: CrmCaseActionRequest,
) -> CrmCase:
    """Wykonuje zmianę stanu i zapisuje pełny ślad zdarzenia."""
    operator = await get_active_operator(session, payload.declared_operator_id)
    now = _utc_now()
    item.declared_operator_id = operator.id
    item.updated_at = now
    event_type = payload.action
    title = "Aktualizacja sprawy"
    description: str | None = None
    event_payload: dict | None = None

    if payload.action == "claim":
        item.owner_user_id = operator.id
        item.owner_name = _user_name(operator)
        item.status = "active"
        item.first_claimed_at = item.first_claimed_at or now
        title = "Sprawa przejęta"
        description = f"Sprawę przejął {_user_name(operator)}."
    elif payload.action == "assign":
        owner = (
            await get_active_operator(session, payload.owner_user_id)
            if payload.owner_user_id
            else None
        )
        item.owner_user_id = owner.id if owner else None
        item.owner_name = _user_name(owner) if owner else (payload.owner_name or "").strip()
        item.status = "active" if item.owner_name else "new"
        item.first_claimed_at = item.first_claimed_at or (now if item.owner_name else None)
        title = "Sprawa przypisana"
        description = f"Nowy właściciel: {item.owner_name or 'kolejka wspólna'}."
        event_payload = {
            "notification_suppressed": True,
            "owner_user_id": item.owner_user_id,
        }
    elif payload.action == "unassign":
        item.owner_user_id = None
        item.owner_name = None
        item.status = "new"
        title = "Sprawa zwrócona do kolejki"
        description = "Usunięto bieżące przypisanie sprawy."
    elif payload.action == "note":
        title = "Notatka pracownika"
        description = (payload.note or "").strip()
    elif payload.action == "close":
        item.status = "done"
        item.terminal_at = now
        title = "Sprawa zakończona"
        description = "Sprawę zamknięto bez uruchamiania komunikacji z klientem."
    elif payload.action == "reopen":
        item.status = "active" if item.owner_name else "new"
        item.terminal_at = None
        item.archived_at = None
        title = "Sprawa ponownie otwarta"
    elif payload.action == "transfer_ms":
        if item.queue != "service_it":
            raise ValueError("Tylko sprawę Serwis + IT można oznaczyć jako przekazaną do MS.")
        item.status = "transferred"
        item.terminal_at = now
        item.ms_order_ref = payload.ms_order_ref or f"LAB-{item.ref[-8:]}"
        title = "Testowe przekazanie do Menadżera Serwisu"
        description = f"Zapisano numer {item.ms_order_ref}. Nie wykonano zapisu w Firebird."
        event_payload = {"firebird_write": False}
    elif payload.action == "meter_update":
        if item.queue != "contracts" or item.category != "meters":
            raise ValueError(
                "Liczniki można zapisać wyłącznie w kategorii Liczniki kolejki Umowy i liczniki."
            )
        item.status = "done"
        item.terminal_at = now
        title = "Testowy odczyt liczników"
        description = "Odczyty zapisano w audycie sprawy, bez aktualizacji Firebird."
        event_payload = {"meters": payload.meters, "firebird_write": False}

    item.events.append(
        _event(
            event_type=event_type,
            title=title,
            description=description,
            actor=operator,
            payload=event_payload,
        )
    )
    await record_audit(
        session,
        user_id=operator.id,
        action=f"crm_case_{payload.action}",
        payload={"case_ref": item.ref, "is_lab": item.is_lab},
    )
    await session.flush()
    return item


async def reset_lab_cases(
    session: AsyncSession,
    *,
    declared_operator_id: int,
    reason: str,
) -> tuple[int, int]:
    """Usuwa wyłącznie rekordy LAB i zachowuje zagregowane zdarzenie audytowe."""
    operator = await get_active_operator(session, declared_operator_id)
    case_ids = list(
        (await session.scalars(select(CrmCase.id).where(CrmCase.is_lab.is_(True)))).all()
    )
    if not case_ids:
        deleted_events = 0
        deleted_cases = 0
    else:
        deleted_events = int(
            await session.scalar(
                select(func.count(CrmCaseEvent.id)).where(CrmCaseEvent.case_id.in_(case_ids))
            )
            or 0
        )
        await session.execute(delete(CrmCaseEvent).where(CrmCaseEvent.case_id.in_(case_ids)))
        result = await session.execute(delete(CrmCase).where(CrmCase.id.in_(case_ids)))
        deleted_cases = int(result.rowcount or 0)
    await record_audit(
        session,
        user_id=operator.id,
        action="crm_lab_reset",
        payload={
            "deleted_cases": deleted_cases,
            "deleted_events": deleted_events,
            "reason": reason,
        },
    )
    return deleted_cases, deleted_events


async def purge_expired_cases(session: AsyncSession) -> tuple[int, int]:
    """Usuwa sprawy po retencji i zapisuje wyłącznie zagregowany ślad."""
    now = _utc_now()
    case_ids = list(
        (await session.scalars(select(CrmCase.id).where(CrmCase.retained_until <= now))).all()
    )
    if not case_ids:
        return 0, 0
    event_count = int(
        await session.scalar(
            select(func.count(CrmCaseEvent.id)).where(CrmCaseEvent.case_id.in_(case_ids))
        )
        or 0
    )
    await session.execute(delete(CrmCaseEvent).where(CrmCaseEvent.case_id.in_(case_ids)))
    result = await session.execute(delete(CrmCase).where(CrmCase.id.in_(case_ids)))
    case_count = int(result.rowcount or 0)
    await record_audit(
        session,
        user_id=None,
        action="crm_retention_purge",
        payload={"deleted_cases": case_count, "deleted_events": event_count},
    )
    return case_count, event_count
