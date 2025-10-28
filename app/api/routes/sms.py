"""Operacje związane z wysyłką i szablonami SMS."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_id, get_db_session
from app.core.config import settings
from app.models import Call, SmsOut, SmsTemplate
from app.schemas.sms import (
    SmsAccountSummary,
    SmsHistoryItem,
    SmsSendRequest,
    SmsTemplateCreate,
    SmsTemplateRead,
)

router = APIRouter(prefix="/sms", tags=["sms"])


def _map_sms(model: SmsOut) -> SmsHistoryItem:
    return SmsHistoryItem(
        id=model.id,
        created_at=model.created_at,
        status=model.status,
        provider_status=model.provider_status,
        sender=str(model.created_by) if model.created_by is not None else None,
        template_id=model.template_id,
        template_name=model.template.name if model.template else None,
        text=model.text,
    )


async def _resolve_template(session: AsyncSession, template_id: int, user_id: int) -> SmsTemplate:
    stmt = select(SmsTemplate).where(SmsTemplate.id == template_id)
    result = await session.execute(stmt)
    template = result.scalar_one_or_none()
    if template is None or (template.scope != "global" and template.owner_id != user_id):
        raise HTTPException(status_code=404, detail="Szablon nie istnieje lub jest niedostępny")
    if not template.is_active:
        raise HTTPException(status_code=400, detail="Szablon jest nieaktywny")
    return template


@router.get("/templates", response_model=list[SmsTemplateRead])
async def list_templates(
    include_inactive: bool = Query(False),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    user_id: int = Depends(get_current_user_id),
) -> list[SmsTemplateRead]:
    """Lista szablonów dostępnych dla użytkownika (globalne + własne)."""
    stmt = (
        select(SmsTemplate)
        .where(
            or_(
                SmsTemplate.scope == "global",
                SmsTemplate.owner_id == user_id,
            )
        )
        .order_by(SmsTemplate.scope.desc(), SmsTemplate.name.asc())
    )
    if not include_inactive:
        stmt = stmt.where(SmsTemplate.is_active.is_(True))

    result = await session.execute(stmt)
    return list(result.scalars())


@router.post("/templates", response_model=SmsTemplateRead, status_code=status.HTTP_201_CREATED)
async def create_template(
    payload: SmsTemplateCreate,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    user_id: int = Depends(get_current_user_id),
) -> SmsTemplateRead:
    """Tworzy nowy szablon (globalny tylko dla administratora)."""
    scope = payload.scope or "user"
    owner_id: int | None = None

    if scope == "global" and user_id != 1:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Brak uprawnień")
    if scope != "global":
        owner_id = user_id

    template = SmsTemplate(
        name=payload.name,
        body=payload.body,
        scope=scope,
        owner_id=owner_id,
        is_active=payload.is_active,
        created_by=user_id,
        updated_by=user_id,
    )
    session.add(template)
    await session.commit()
    await session.refresh(template)
    return template


@router.post("/send", response_model=SmsHistoryItem, status_code=status.HTTP_201_CREATED)
async def enqueue_sms(
    payload: SmsSendRequest,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    user_id: int = Depends(get_current_user_id),
) -> SmsHistoryItem:
    """Dodaje wiadomość SMS do kolejki `ctip.sms_out`."""
    if payload.call_id is not None:
        call = await session.get(Call, payload.call_id)
        if call is None:
            raise HTTPException(status_code=404, detail="Połączenie nie istnieje")

    resolved_text = payload.text
    template: SmsTemplate | None = None
    if payload.template_id is not None:
        template = await _resolve_template(session, payload.template_id, user_id)
        resolved_text = template.body

    sms = SmsOut(
        dest=payload.dest,
        text=resolved_text or "",
        call_id=payload.call_id,
        template_id=payload.template_id,
        origin=payload.origin or (template.scope if template else "ui"),
        source="ui",
        status="NEW",
        created_by=user_id,
        provider_status=None,
        provider_msg_id=None,
    )
    session.add(sms)
    await session.commit()
    await session.refresh(sms)

    return _map_sms(sms)


@router.get("/account", response_model=SmsAccountSummary)
async def get_account_summary(
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    _: int = Depends(get_current_user_id),
) -> SmsAccountSummary:
    """Zwraca podstawowe statystyki dotyczące konta SMS."""
    sent_q = await session.execute(select(func.count(SmsOut.id)).where(SmsOut.status == "SENT"))
    pending_q = await session.execute(select(func.count(SmsOut.id)).where(SmsOut.status == "NEW"))
    failed_q = await session.execute(select(func.count(SmsOut.id)).where(SmsOut.status == "ERROR"))
    latest_template_q = await session.execute(select(func.max(SmsTemplate.updated_at)))

    return SmsAccountSummary(
        default_sender=settings.sms_default_sender,
        total_sent=int(sent_q.scalar() or 0),
        pending=int(pending_q.scalar() or 0),
        failed=int(failed_q.scalar() or 0),
        last_template_update=latest_template_q.scalar(),
    )


@router.get("/history", response_model=list[SmsHistoryItem])
async def sms_history(
    dest: str | None = Query(default=None),
    call_id: int | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=500),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    _: int = Depends(get_current_user_id),
) -> list[SmsHistoryItem]:
    """Zwraca historię SMS z opcjonalnym filtrowaniem po numerze, ID połączenia i statusie."""
    stmt = select(SmsOut).order_by(SmsOut.created_at.desc()).limit(limit)

    if dest:
        stmt = stmt.where(SmsOut.dest == dest)
    if call_id is not None:
        stmt = stmt.where(SmsOut.call_id == call_id)
    if status_filter:
        stmt = stmt.where(SmsOut.status == status_filter)

    result = await session.execute(stmt)
    rows = result.scalars().all()
    return [_map_sms(row) for row in rows]
