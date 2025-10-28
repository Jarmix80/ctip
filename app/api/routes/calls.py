"""Trasy obsługujące listę połączeń CTIP."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.models import Call, CallEvent, Contact, SmsOut
from app.schemas.call import CallDetail, CallEventSchema, CallFilters, CallListItem
from app.schemas.contact import ContactSchema
from app.schemas.sms import SmsHistoryItem

router = APIRouter(prefix="/calls", tags=["calls"])


def _map_contact(model: Contact | None) -> ContactSchema | None:
    if not model:
        return None
    return ContactSchema(
        id=model.id,
        number=model.number,
        ext=model.ext,
        first_name=model.first_name,
        last_name=model.last_name,
        company=model.company,
        nip=model.nip,
        email=model.email,
        notes=model.notes,
        source=model.source,
        created_at=model.created_at,
        updated_at=model.updated_at,
        devices=[],
    )


def _map_sms(model: SmsOut | None) -> SmsHistoryItem | None:
    if not model:
        return None
    return SmsHistoryItem(
        id=model.id,
        created_at=model.created_at,
        status=model.status,
        provider_status=model.provider_status,
        sender=str(model.created_by) if model.created_by is not None else None,
        template_id=model.template_id,
        template_name=model.template.name if getattr(model, "template", None) else None,
        text=model.text,
    )


def _map_sms_many(models: Iterable[SmsOut]) -> list[SmsHistoryItem]:
    return [
        SmsHistoryItem(
            id=item.id,
            created_at=item.created_at,
            status=item.status,
            provider_status=item.provider_status,
            sender=str(item.created_by) if item.created_by is not None else None,
            template_id=item.template_id,
            template_name=item.template.name if getattr(item, "template", None) else None,
            text=item.text,
        )
        for item in models
    ]


@router.get("", response_model=list[CallListItem])
async def list_calls(
    direction: str | None = Query(default=None),  # noqa: B008
    status: str | None = Query(default=None),  # noqa: B008
    ext: str | None = Query(default=None),  # noqa: B008
    search: str | None = Query(default=None),  # noqa: B008
    date_from: datetime | None = Query(default=None),  # noqa: B008
    date_to: datetime | None = Query(default=None),  # noqa: B008
    limit: int = Query(default=50, ge=1, le=500),  # noqa: B008
    offset: int = Query(default=0, ge=0),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> list[CallListItem]:
    """Zwraca listę połączeń wraz z informacją o ostatnim SMS i kontakcie."""
    filters = CallFilters(
        direction=direction,
        status=status,
        ext=ext,
        search=search,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )

    stmt: Select[tuple[Call]] = select(Call).order_by(Call.started_at.desc())

    if filters.direction:
        stmt = stmt.where(Call.direction == filters.direction)
    if filters.status:
        stmt = stmt.where(Call.disposition == filters.status)
    if filters.ext:
        stmt = stmt.where(Call.ext == filters.ext)
    if filters.date_from:
        stmt = stmt.where(Call.started_at >= filters.date_from)
    if filters.date_to:
        stmt = stmt.where(Call.started_at <= filters.date_to)
    if filters.search:
        needle = f"%{filters.search.lower()}%"
        search_clauses = [func.lower(Call.number).like(needle)]
        if hasattr(Call, "notes"):
            search_clauses.append(func.lower(Call.notes).like(needle))
        stmt = stmt.where(or_(*search_clauses))

    stmt = stmt.offset(filters.offset).limit(filters.limit)

    calls_result = await session.execute(stmt)
    call_models = calls_result.scalars().all()

    if not call_models:
        return []

    # Pobranie kontaktów dla numerów w jednym zapytaniu
    numbers = {call.number for call in call_models if call.number}
    contacts_map: dict[str, Contact] = {}
    if numbers:
        contacts_result = await session.execute(select(Contact).where(Contact.number.in_(numbers)))
        contacts = contacts_result.scalars().all()
        contacts_map = {c.number: c for c in contacts}

    # Pobranie ostatniego sms per call_id
    call_ids = [call.id for call in call_models]
    sms_map: dict[int, SmsOut] = {}
    if call_ids:
        sms_stmt = (
            select(SmsOut)
            .where(SmsOut.call_id.in_(call_ids))
            .order_by(SmsOut.call_id, SmsOut.created_at.desc())
        )
        sms_rows = await session.execute(sms_stmt)
        for sms in sms_rows.scalars():
            if sms.call_id is None:
                continue
            if sms.call_id not in sms_map:
                sms_map[sms.call_id] = sms

    items: list[CallListItem] = []
    for model in call_models:
        contact = contacts_map.get(model.number or "")
        items.append(
            CallListItem(
                id=model.id,
                direction=model.direction,
                ext=model.ext,
                ext_name=None,
                number=model.number,
                started_at=model.started_at,
                duration_s=model.duration_s,
                disposition=model.disposition,
                latest_sms=_map_sms(sms_map.get(model.id)),
                contact=_map_contact(contact),
            )
        )

    return items


@router.get("/{call_id}", response_model=CallDetail)
async def get_call_detail(
    call_id: int,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> CallDetail:
    """Zwraca szczegóły połączenia wraz z osią czasu i historią SMS."""
    call_result = await session.execute(select(Call).where(Call.id == call_id))
    call = call_result.scalar_one_or_none()
    if not call:
        raise HTTPException(status_code=404, detail="Połączenie nie istnieje")

    contact = None
    if call.number:
        contact_result = await session.execute(select(Contact).where(Contact.number == call.number))
        contact = contact_result.scalar_one_or_none()

    events_result = await session.execute(
        select(CallEvent).where(CallEvent.call_id == call.id).order_by(CallEvent.ts.asc())
    )
    events = events_result.scalars().all()

    sms_result = await session.execute(
        select(SmsOut).where(SmsOut.call_id == call.id).order_by(SmsOut.created_at.desc())
    )
    sms_items = sms_result.scalars().all()

    return CallDetail(
        id=call.id,
        direction=call.direction,
        ext=call.ext,
        ext_name=None,
        number=call.number,
        started_at=call.started_at,
        duration_s=call.duration_s,
        disposition=call.disposition,
        latest_sms=_map_sms(sms_items[0] if sms_items else None),
        contact=_map_contact(contact),
        connected_at=call.connected_at,
        ended_at=call.ended_at,
        notes=call.notes,
        events=[
            CallEventSchema(
                ts=event.ts,
                typ=event.typ,
                payload=event.payload,
                ext=event.ext,
                number=event.number,
            )
            for event in events
        ],
        sms_history=_map_sms_many(sms_items),
    )
